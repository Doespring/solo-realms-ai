"""Persistent integer-microcurrency ledger; reservations/one-shot overrides precede sending."""
import hashlib,json,sqlite3,time,uuid
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from decimal import Decimal,ROUND_CEILING

def micros(value):return int((Decimal(str(value))*1000000).to_integral_value(rounding=ROUND_CEILING))
def amount(value):return str(Decimal(value)/1000000)

def token_count(value):
    return value if isinstance(value,int) and not isinstance(value,bool) and 0<=value<=1000000000 else None

def tariff(value):
    try:
        value=Decimal(str(value))
        if not value.is_finite() or not 0<=value<=10000000:raise ValueError('Неверный тариф модели')
        return value
    except (ArithmeticError,TypeError,ValueError):raise ValueError('Неверный тариф модели') from None


def cache_tariff(config,input_rate):
    modern=config.cache_read_per_million;legacy=config.cached_input_per_million
    if modern is not None and legacy is not None and tariff(modern)!=tariff(legacy):
        raise ValueError('Конфликт тарифов кэшированного входа')
    explicit=modern if modern is not None else legacy
    return tariff(explicit) if explicit is not None else input_rate/10

def usage_counts(usage):
    """Recognized cache counters are subsets of total input, never extra tokens.

    OpenAI-compatible prompt/input details and DeepSeek-style hit/miss fields.
    Unsupported cache formats do not establish a discount. Conflicting aliases,
    malformed types and counters outside total input disable cache pricing.
    """
    def total(primary,alias):
        value=token_count(usage.get(primary,usage.get(alias)))
        if primary in usage and alias in usage and token_count(usage[alias])!=value:return None
        return value
    inp=total('prompt_tokens','input_tokens')
    out=total('completion_tokens','output_tokens')
    exclusive_cache=any(name in usage for name in ('cache_read_input_tokens','cache_creation_input_tokens'))
    if exclusive_cache:inp=None  # Native/exclusive accounting is not this adapter's inclusive contract.
    detail=usage.get('completion_tokens_details',usage.get('output_tokens_details',{}))
    reasoning=token_count(detail.get('reasoning_tokens',usage.get('reasoning_tokens'))) if isinstance(detail,dict) else None
    if out is not None and reasoning is not None and reasoning>out:reasoning=None
    candidates=[];invalid=exclusive_cache
    for name in ('prompt_tokens_details','input_tokens_details'):
        detail=usage.get(name)
        if detail is not None and not isinstance(detail,dict):invalid=True
        elif isinstance(detail,dict) and 'cached_tokens' in detail:candidates.append(token_count(detail['cached_tokens']))
    if 'prompt_cache_hit_tokens' in usage:candidates.append(token_count(usage['prompt_cache_hit_tokens']))
    if candidates:
        invalid=invalid or inp is None or any(v is None or v>inp for v in candidates)
        invalid=invalid or len(set(candidates))!=1
    if 'prompt_cache_miss_tokens' in usage and candidates and not invalid:
        miss=token_count(usage['prompt_cache_miss_tokens'])
        invalid=miss is None or miss+candidates[0]!=inp
    cached=candidates[0] if candidates and not invalid else None
    return inp,out,reasoning,cached,'invalid' if invalid else 'provider' if candidates else 'not_reported'

class BudgetExceeded(RuntimeError):
    def __init__(self,approval,quote,currency,reasons=None):
        self.approval=approval;self.quote=amount(quote);self.currency=currency
        self.reasons=reasons or []
        super().__init__('Лимит расходов превышен. '+(' '.join(self.reasons)+' ' if self.reasons else '')+'Требуется разрешить именно этот запрос: '+self.quote+' '+currency)

class CostLedger:
    def __init__(self,path):
        from pathlib import Path
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,campaign TEXT,session TEXT NOT NULL,turn_ref TEXT NOT NULL,
                provider TEXT NOT NULL,model TEXT NOT NULL,purpose TEXT NOT NULL,currency TEXT NOT NULL,day TEXT NOT NULL,created REAL NOT NULL,
                reserved INTEGER NOT NULL,cost INTEGER NOT NULL DEFAULT 0,cost_source TEXT NOT NULL DEFAULT 'reserved_estimate',status TEXT NOT NULL DEFAULT 'reserved',
                input_tokens INTEGER,output_tokens INTEGER,reasoning_tokens INTEGER,usage_source TEXT NOT NULL DEFAULT 'estimated',
                ttft_ms REAL,duration_ms REAL,error_code TEXT NOT NULL DEFAULT '',retry_of TEXT);
                CREATE INDEX IF NOT EXISTS calls_daily ON calls(currency,day);
                CREATE INDEX IF NOT EXISTS calls_session ON calls(currency,session);
                CREATE INDEX IF NOT EXISTS calls_campaign ON calls(campaign,turn_ref);
                CREATE INDEX IF NOT EXISTS calls_created ON calls(created);
                CREATE INDEX IF NOT EXISTS calls_model ON calls(provider,model);
                CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,quote INTEGER NOT NULL,currency TEXT NOT NULL,
                purpose TEXT NOT NULL,model TEXT NOT NULL,campaign TEXT,turn_ref TEXT NOT NULL,expires REAL NOT NULL,status TEXT NOT NULL DEFAULT 'pending');''')
            # A crash cannot establish whether the provider billed an in-flight request.
            c.execute("UPDATE calls SET status='interrupted',cost=reserved,cost_source='reserved_estimate' WHERE status='reserved'")
            columns={r['name'] for r in c.execute('PRAGMA table_info(calls)')}
            for name in ('requested_endpoint','actual_endpoint','prompt_version','thinking_control','price_checked_at','schema_mode'):
                if name not in columns:c.execute('ALTER TABLE calls ADD COLUMN '+name+" TEXT NOT NULL DEFAULT ''")
            for name,definition in {
                'cached_input_tokens':'INTEGER', 'cached_tokens':'INTEGER', 'cache_usage_status':"TEXT NOT NULL DEFAULT 'not_reported'",
                'cache_price_applied':'INTEGER NOT NULL DEFAULT 0', 'input_estimate_details':"TEXT NOT NULL DEFAULT '{}'",
                'input_rate':"TEXT NOT NULL DEFAULT ''", 'output_rate':"TEXT NOT NULL DEFAULT ''",
                'cached_input_rate':"TEXT NOT NULL DEFAULT ''",
            }.items():
                if name not in columns:c.execute('ALTER TABLE calls ADD COLUMN '+name+' '+definition)
            c.execute('UPDATE calls SET cached_tokens=cached_input_tokens WHERE cached_tokens IS NULL AND cached_input_tokens IS NOT NULL')
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=15);c.row_factory=sqlite3.Row;c.execute('PRAGMA journal_mode=WAL')
        try:yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()
    def reserve(self,profile,model_config,body,*,campaign,session,turn_ref,purpose,input_estimate,estimate_details=None):
        if model_config.input_per_million is None or model_config.output_per_million is None:
            raise ValueError('Неизвестна стоимость модели. Укажите проверенные тарифы перед запросом')
        inp_rate,out_rate=tariff(model_config.input_per_million),tariff(model_config.output_per_million)
        cache_rate=cache_tariff(model_config,inp_rate)
        # Aggregate vision cache usage does not establish a text-only discount.
        if (estimate_details or {}).get('image_count',0):cache_rate=None
        if token_count(input_estimate) is None or token_count(body[model_config.max_token_parameter]) is None:
            raise ValueError('Неверная оценка числа токенов')
        # A cheap cache tariff never reduces the pre-request reservation. A more
        # expensive explicit cache tariff is covered by the worst-case rate.
        quote=micros((Decimal(input_estimate)*max(inp_rate,cache_rate if cache_rate is not None else inp_rate)+Decimal(body[model_config.max_token_parameter])*out_rate)/1000000)
        fingerprint=hashlib.sha256(json.dumps([profile.id,profile.base_url,profile.provider,profile.currency,campaign,session,turn_ref,purpose,body],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        day=datetime.now(timezone(timedelta(hours=profile.day_utc_offset))).date().isoformat()
        approval=None
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            def spent(clause,args):
                return c.execute("SELECT COALESCE(SUM(CASE WHEN status='reserved' THEN reserved ELSE cost END),0) FROM calls WHERE currency=? AND "+clause,(profile.currency,*args)).fetchone()[0]
            if profile.total_budget is not None and spent('1=1',[])+quote>micros(profile.total_budget):raise ValueError('Общий подтверждённый лимит облачного AI исчерпан. Новый остаток можно разрешить только явным подтверждением в настройках.')
            over=quote>micros(profile.max_request_cost) or spent('session=?',[session])+quote>micros(profile.session_budget) or spent('day=?',[day])+quote>micros(profile.daily_budget)
            if profile.monthly_budget is not None:over=over or spent('substr(day,1,7)=?',[day[:7]])+quote>micros(profile.monthly_budget)
            if purpose in ('preparation_qwen','preparation_world','character_creation','pdf_analyst') and profile.preparation_budget is not None:
                over=over or spent("campaign IS ? AND purpose IN ('preparation_qwen','preparation_world','character_creation','pdf_analyst')",[campaign])+quote>micros(profile.preparation_budget)
            if over:
                granted=c.execute("SELECT id FROM approvals WHERE fingerprint=? AND quote>=? AND currency=? AND expires>? AND status='approved' ORDER BY expires DESC LIMIT 1",(fingerprint,quote,profile.currency,time.time())).fetchone()
                if granted:c.execute("UPDATE approvals SET status='used' WHERE id=?",(granted['id'],))
                else:
                    approval=str(uuid.uuid4())
                    c.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?)',(approval,fingerprint,quote,profile.currency,purpose,body['model'],campaign,turn_ref,time.time()+600,'pending'))
            if approval is None:
                call=str(uuid.uuid4())
                previous=c.execute('SELECT id FROM calls WHERE campaign IS ? AND turn_ref=? AND purpose=? ORDER BY created DESC LIMIT 1',(campaign,turn_ref,purpose)).fetchone()
                c.execute('''INSERT INTO calls(id,campaign,session,turn_ref,provider,model,purpose,currency,day,created,reserved,input_tokens,retry_of)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(call,campaign,session,turn_ref,profile.provider,body['model'],purpose,profile.currency,day,time.time(),quote,input_estimate,previous['id'] if previous else None))
                c.execute('UPDATE calls SET input_estimate_details=?,input_rate=?,output_rate=?,cached_input_rate=?,price_checked_at=? WHERE id=?',
                          (json.dumps(estimate_details or {},sort_keys=True),str(inp_rate),str(out_rate),str(cache_rate) if cache_rate is not None else '',model_config.price_checked_at,call))
        if approval:
            labels=[('max_request_cost','запроса'),('session_budget','сессии'),('daily_budget','дня'),('monthly_budget','месяца')]
            if purpose in ('preparation_qwen','preparation_world','character_creation','pdf_analyst'):labels.append(('preparation_budget','подготовки'))
            raise BudgetExceeded(approval,quote,profile.currency,['Бюджет '+label+' равен 0.' for field,label in labels if getattr(profile,field)==0])
        return call,quote
    def approve(self,approval):
        with self.db() as c:
            if c.execute("UPDATE approvals SET status='approved' WHERE id=? AND status='pending' AND expires>?",(approval,time.time())).rowcount!=1:
                raise ValueError('Подтверждение устарело или уже использовано')
    def pending(self):
        with self.db() as c:
            return [dict(id=r['id'],quote=amount(r['quote']),currency=r['currency'],purpose=r['purpose'],model=r['model'],campaign=r['campaign'],turn_ref=r['turn_ref']) for r in c.execute("SELECT * FROM approvals WHERE status='pending' AND expires>? ORDER BY expires DESC LIMIT 20",(time.time(),))]
    def contract_error(self,call,code):
        with self.db() as c:c.execute("UPDATE calls SET status='invalid_contract',error_code=? WHERE id=?",(code,call))
    def finish(self,call,profile,config,usage,metric,*,definitely_not_sent=False):
        usage=usage if isinstance(usage,dict) else {}
        input_n,output_n,reasoning,cached,cache_status=usage_counts(usage)
        with self.db() as c:
            old=c.execute('SELECT * FROM calls WHERE id=?',(call,)).fetchone()
            cache_applied=False
            cost=old['reserved'];source='reserved_estimate';actual_cost=usage.get('cost')
            try:
                actual_cost=Decimal(str(actual_cost)) if actual_cost is not None else None
                if actual_cost is not None and (not actual_cost.is_finite() or actual_cost<0 or actual_cost>1000000):actual_cost=None
            except Exception:actual_cost=None
            if definitely_not_sent:cost=0;source='not_sent'
            elif actual_cost is not None and usage.get('currency',profile.usage_cost_currency)==profile.currency:
                cost=micros(actual_cost);source='provider'
            elif input_n is not None and output_n is not None:
                try:
                    inp_rate=tariff(old['input_rate'] or config.input_per_million)
                    out_rate=tariff(old['output_rate'] or config.output_per_million)
                    # New calls use the tariff captured at reservation time. A
                    # legacy row cannot establish a historical cached tariff.
                    cache_rate=tariff(old['cached_input_rate']) if old['cached_input_rate'] else None
                    cache_applied=cached is not None and cached>0 and cache_rate is not None
                    cached_billed=cached if cache_applied else 0
                    cost=micros((Decimal(input_n-cached_billed)*inp_rate+Decimal(cached_billed)*(cache_rate if cache_applied else inp_rate)+Decimal(output_n)*out_rate)/1000000)
                    source='calculated_from_usage'
                except ValueError:pass  # Keep the conservative reservation when rates are unknown.
            c.execute('''UPDATE calls SET cost=?,cost_source=?,status=?,input_tokens=?,output_tokens=?,reasoning_tokens=?,usage_source=?,ttft_ms=?,duration_ms=?,error_code=? WHERE id=?''',
                (cost,source,metric['status'],input_n if input_n is not None else old['input_tokens'],output_n,reasoning,
                 'provider' if input_n is not None and output_n is not None else 'estimated',metric.get('ttft_ms'),metric.get('duration_ms'),metric.get('error_code',''),call))
            c.execute('UPDATE calls SET requested_endpoint=?,actual_endpoint=?,prompt_version=?,thinking_control=?,schema_mode=?,price_checked_at=? WHERE id=?',
                      (metric.get('requested_endpoint',''),metric.get('actual_endpoint',''),metric.get('prompt_version') or '',metric.get('thinking_control',''),metric.get('schema_mode',''),old['price_checked_at'] if old['input_rate'] else config.price_checked_at,call))
            c.execute('UPDATE calls SET cached_input_tokens=?,cached_tokens=?,cache_usage_status=?,cache_price_applied=? WHERE id=?',
                      (cached,cached,cache_status,int(cache_applied),call))
            return dict(cost=amount(cost),cost_source=source,currency=profile.currency,input_tokens=input_n,output_tokens=output_n,reasoning_tokens=reasoning,
                        cached_input_tokens=cached,cached_tokens=cached,cache_usage_status=cache_status,cache_price_applied=cache_applied)
    # One aggregate expression list for every grouping level. The reserved
    # amount is the conservative charge for an in-flight call, the settled
    # amount for everything else; this is the same rule the budget checks use.
    _group_totals=("COUNT(*) AS calls,"
        " COALESCE(SUM(CASE WHEN status='reserved' THEN reserved ELSE cost END),0) AS cost_micros,"
        " COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens,"
        " COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens,"
        " SUM(CASE WHEN cost_source!='provider' THEN 1 ELSE 0 END) AS estimated_calls")
    def groups(self,c,keys,where='',args=()):
        """Aggregate in SQLite: the full history never enters Python memory.

        Row order stays "most recently used group first" (the legacy
        ORDER BY created DESC / first-seen rule), so callers see the same list.
        """
        selected=', '.join(list(keys)+['currency'])
        result=[]
        for row in c.execute('SELECT '+selected+', '+self._group_totals+' FROM calls'+(' WHERE '+where if where else '')+
                             ' GROUP BY '+selected+' ORDER BY MAX(created) DESC, '+selected,args):
            item={k:row[k] for k in keys}
            item.update(currency=row['currency'],calls=row['calls'],input_tokens=row['input_tokens'],
                        output_tokens=row['output_tokens'],cached_input_tokens=row['cached_input_tokens'],
                        estimated_calls=row['estimated_calls'],cost=amount(row['cost_micros']))
            result.append(item)
        return result
    def summary(self,campaign=None,session=None,turn_ref=None):
        with self.db() as c:
            total=self.groups(c,[])
            campaign_groups=self.groups(c,[],'campaign IS ?',(campaign,)) if campaign else []
            session_groups=self.groups(c,[],'session IS ?',(session,)) if session else []
            models=self.groups(c,['provider','model'])
            turns=self.groups(c,['turn_ref'],'campaign IS ?',(campaign,)) if campaign else []
            current_ref=turn_ref
            if not current_ref and campaign:
                latest=c.execute('SELECT turn_ref FROM calls WHERE campaign IS ? ORDER BY created DESC LIMIT 1',(campaign,)).fetchone()
                current_ref=latest['turn_ref'] if latest else None
            current=self.groups(c,[],'campaign IS ? AND turn_ref IS ?',(campaign,current_ref)) if campaign and current_ref is not None else []
            averages=[] if not campaign else [
                dict(currency=row['currency'],turns=row['turns'],average=str(Decimal(row['cost_micros'])/1000000/row['turns']))
                for row in c.execute("SELECT currency,COUNT(DISTINCT turn_ref) AS turns,"
                                     " COALESCE(SUM(CASE WHEN status='reserved' THEN reserved ELSE cost END),0) AS cost_micros"
                                     " FROM calls WHERE campaign IS ? GROUP BY currency ORDER BY currency",(campaign,))
                if row['turns']]
            # The public list is capped at the latest 100 calls; LIMIT keeps the
            # rest of the history on disk. Only this slice is materialised.
            rows=c.execute('SELECT * FROM calls ORDER BY created DESC LIMIT 100').fetchall()
        def public_call(row):
            try:planned=json.loads(row['input_estimate_details']).get('input_tokens_estimate')
            except (ValueError,TypeError,AttributeError):planned=None
            # Legacy input_tokens may contain a planning estimate. Do not label
            # it actual without complete validated provider usage.
            return dict(row,cost=amount(row['cost']),reserved=amount(row['reserved']),
                        planned_input_tokens=token_count(planned),
                        actual_input_tokens=row['input_tokens'] if row['usage_source']=='provider' else None,
                        actual_output_tokens=row['output_tokens'] if row['usage_source']=='provider' else None)
        return dict(total=total,campaign=campaign_groups,session=session_groups,models=models,turns=turns,
                    current_turn=current,average_per_turn=averages,
                    calls=[public_call(r) for r in rows],pending=self.pending())

    def quotas(self,profile,session):
        day=datetime.now(timezone(timedelta(hours=profile.day_utc_offset))).date().isoformat()
        with self.db() as c:
            rows=c.execute('SELECT session,day,status,cost,reserved FROM calls WHERE currency=?',(profile.currency,)).fetchall()
        result=[]
        for kind,limit,matched in [('session',profile.session_budget,[r for r in rows if r['session']==session]),('day',profile.daily_budget,[r for r in rows if r['day']==day])]:
            spent=sum(r['reserved'] if r['status']=='reserved' else r['cost'] for r in matched)
            cap=micros(limit);fraction=spent/cap if cap else None
            result.append(dict(kind=kind,limit=amount(cap),spent=amount(spent),currency=profile.currency,
                               state='blocked' if spent>=cap and cap else 'disabled' if cap==0 else 'warning' if fraction>=.8 else 'within_budget'))
        return result
