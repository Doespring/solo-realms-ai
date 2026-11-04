import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from .contracts import Hero, Master, Plan
from .version import SCHEMA_VERSION
from . import rules
from .memory import Memory,SCHEMA as MEMORY_SCHEMA

def uid():
    return str(uuid.uuid4())

def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',',':'))

START = {
    'location':'Таверна «Последний огонь»', 'turn':0,
    'locations':[
        {'name':'Таверна «Последний огонь»','description':'Огонь в очаге, запах хвои. Ирина ждёт у стойки.',
         'npc':[{'name':'Ирина','character':'Осторожная трактирщица, говорит коротко и прямо. Хочет найти брата-гонца.',
                 'known':'Брат ушёл к старой башне вчера и не вернулся.'}],
         'objects':['стойка','очаг','окно','письмо'],
         'facts':['Ирина просит найти её брата. На письме брата изображены три сосны.']},
        {'name':'Старая башня','description':'Каменная башня среди сосен. Вход закрыт. У тропы стоит местный страж.',
         'npc':[{'name':'Страж','character':'Усталый и вежливый. Боится ночных звуков. Не нападает без причины.',
                 'known':'Ночью в башне горел свет.'}],
         'objects':['дверь','следы','сосны'],
         'facts':['Дверь закрыта. Следы ведут от башни к реке.']},
        {'name':'Берег реки','description':'Речная вода тихо движется между корней. У берега стоит лодка.',
         'npc':[], 'objects':['лодка','следы','вода'],
         'facts':['На борту лодки знак трёх сосен. Гонца на берегу не видно.']}
    ],
    'facts':[], 'deferred':[]
}

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.memory = Memory(self)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            previous_schema=c.execute('PRAGMA user_version').fetchone()[0]
            if previous_schema>SCHEMA_VERSION:
                raise ValueError('База создана более новой версией приложения; миграция назад запрещена')
            c.executescript('''
            CREATE TABLE IF NOT EXISTS campaigns(id TEXT PRIMARY KEY,name TEXT NOT NULL,state TEXT NOT NULL,
              master TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,campaign TEXT NOT NULL REFERENCES campaigns(id),
              request_id TEXT NOT NULL,original TEXT NOT NULL,plan TEXT NOT NULL,status TEXT NOT NULL,
              version INTEGER NOT NULL,metrics TEXT NOT NULL DEFAULT '{}',created REAL NOT NULL,
              UNIQUE(campaign,request_id));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,
              action TEXT UNIQUE NOT NULL REFERENCES actions(id),campaign TEXT NOT NULL REFERENCES campaigns(id),
              before_state TEXT NOT NULL,after_state TEXT NOT NULL,results TEXT NOT NULL,
              narrative TEXT NOT NULL DEFAULT '',narrative_status TEXT NOT NULL DEFAULT 'pending',
              error TEXT NOT NULL DEFAULT '',undone INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY,campaign TEXT NOT NULL REFERENCES campaigns(id),
              name TEXT NOT NULL,state TEXT NOT NULL,seq INTEGER NOT NULL,created REAL NOT NULL,active TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS master_versions(id INTEGER PRIMARY KEY AUTOINCREMENT,
              campaign TEXT NOT NULL REFERENCES campaigns(id),master TEXT NOT NULL,created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS event_campaign ON events(campaign,seq);
            CREATE TABLE IF NOT EXISTS recovery_events(id TEXT PRIMARY KEY,campaign TEXT NOT NULL REFERENCES campaigns(id),
              event TEXT NOT NULL,game_minutes INTEGER NOT NULL,seed INTEGER NOT NULL,engine TEXT NOT NULL,
              result TEXT NOT NULL,delta TEXT NOT NULL,created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS recovery_campaign ON recovery_events(campaign,game_minutes);
            CREATE TABLE IF NOT EXISTS state_operations(id TEXT PRIMARY KEY,campaign TEXT NOT NULL REFERENCES campaigns(id),
              kind TEXT NOT NULL,before_state TEXT NOT NULL,after_state TEXT NOT NULL,reference TEXT NOT NULL,created REAL NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS journal_fts USING fts5(event_id UNINDEXED,campaign UNINDEXED,content);
            CREATE TABLE IF NOT EXISTS master_profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL,master TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS profile_versions(id TEXT PRIMARY KEY,profile TEXT NOT NULL,
              name TEXT NOT NULL,master TEXT NOT NULL,operation TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS ruleset_versions(id TEXT NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS ruleset_conversions(token TEXT PRIMARY KEY,campaign TEXT NOT NULL REFERENCES campaigns(id),
              base_version INTEGER NOT NULL,state_hash TEXT NOT NULL,payload TEXT NOT NULL,expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS trade_offers(id TEXT PRIMARY KEY,campaign TEXT NOT NULL REFERENCES campaigns(id),
              request_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,expires REAL NOT NULL,created REAL NOT NULL,
              UNIQUE(campaign,request_id));
            CREATE TABLE IF NOT EXISTS narrative_versions(id TEXT PRIMARY KEY,event TEXT NOT NULL REFERENCES events(id),
              old_text TEXT NOT NULL,new_text TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS gm_messages(id TEXT PRIMARY KEY,campaign TEXT NOT NULL REFERENCES campaigns(id),
              request_id TEXT NOT NULL,question TEXT NOT NULL,answer TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'pending',
              metrics TEXT NOT NULL DEFAULT '[]',created REAL NOT NULL,UNIQUE(campaign,request_id));
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,campaign TEXT NOT NULL REFERENCES campaigns(id),
              request_id TEXT NOT NULL,original TEXT NOT NULL,reply TEXT NOT NULL,kind TEXT NOT NULL,
              context TEXT NOT NULL DEFAULT '{}',status TEXT NOT NULL DEFAULT 'open',created REAL NOT NULL,
              UNIQUE(campaign,request_id));
            '''+MEMORY_SCHEMA)
            from .tabletop_api import SCHEMA as TABLETOP_SCHEMA
            c.executescript(TABLETOP_SCHEMA)
            # Миграция существующего проекта с ранней схемы прохода 1.
            if 'active' not in {r['name'] for r in c.execute('PRAGMA table_info(checkpoints)')}:
                c.execute("ALTER TABLE checkpoints ADD COLUMN active TEXT NOT NULL DEFAULT '[]'")
                for row in c.execute('SELECT id,campaign,seq FROM checkpoints').fetchall():
                    active=[r[0] for r in c.execute('SELECT id FROM events WHERE campaign=? AND seq<=? AND undone=0',(row['campaign'],row['seq']))]
                    c.execute('UPDATE checkpoints SET active=? WHERE id=?',(dump(active),row['id']))
            if 'narrative_data' not in {r['name'] for r in c.execute('PRAGMA table_info(events)')}:
                c.execute("ALTER TABLE events ADD COLUMN narrative_data TEXT NOT NULL DEFAULT '{}'")
            # Which turn scored a recovery event, so a load or an undo can tell
            # an abandoned future from the turn the active timeline still owns.
            if 'turn' not in {r['name'] for r in c.execute('PRAGMA table_info(recovery_events)')}:
                c.execute('ALTER TABLE recovery_events ADD COLUMN turn TEXT')
            from .profiles import PERSONAL,PERSONAL_NAME
            c.execute('INSERT OR IGNORE INTO master_profiles VALUES(?,?,?,?)',
                      ('personal-gm-v1.1',PERSONAL_NAME,PERSONAL.model_dump_json(),time.time()))
            if previous_schema<6:self.memory.migrate(c)
            from .rulesets import BUILTINS
            for preset in BUILTINS.values():
                c.execute('INSERT OR IGNORE INTO ruleset_versions VALUES(?,?,?,?)',(preset.id,preset.version,preset.model_dump_json(),time.time()))
            c.execute('PRAGMA user_version='+str(SCHEMA_VERSION))
            # Прерванная генерация не имеет права повторять механику после перезапуска.
            c.execute("UPDATE events SET narrative_status='error',error='Генерация прервана перезапуском. Можно повторить описание.' WHERE narrative_status='streaming'")
            c.execute("UPDATE gm_messages SET status='error' WHERE status='streaming'")

    @contextmanager
    def connection(self, write=False):
        c = sqlite3.connect(self.path,timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        c.execute('PRAGMA journal_mode=WAL')
        if write:
            c.execute('BEGIN IMMEDIATE')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def campaign(self, campaign, c=None):
        if c is None:
            with self.connection() as db:
                return self.campaign(campaign,db)
        row = c.execute('SELECT * FROM campaigns WHERE id=?',(campaign,)).fetchone()
        if not row:
            raise KeyError('Кампания не найдена')
        value = dict(row)
        value['state'] = json.loads(value['state'])
        value['master'] = json.loads(value['master'])
        return value

    def create(self,name,hero,ruleset=None,world_settings=None,*,initial_state=None,master=None):
        from .profiles import PERSONAL
        state = json.loads(dump(START if initial_state is None else initial_state))
        state['hero'] = hero.model_dump()
        if ruleset is not None:state['ruleset']=ruleset.model_dump()
        if world_settings is not None:
            from .world import ensure,observe
            state['world']={'settings':world_settings.model_dump()};ensure(state);observe(state)
        campaign = uid()
        with self.connection(True) as c:
            c.execute('INSERT INTO campaigns VALUES(?,?,?,?,?,?)',(campaign,name,dump(state),(master or PERSONAL).model_dump_json(),0,time.time()))
            self.memory.register(c,campaign,state)
        return self.campaign(campaign)

    def list_campaigns(self):
        with self.connection() as c:
            return [dict(r) for r in c.execute('SELECT id,name,version,created FROM campaigns ORDER BY created DESC')]

    @staticmethod
    def decode_action(row):
        if not row:
            return None
        value = dict(row)
        value['plan'] = json.loads(value['plan'])
        value['metrics'] = json.loads(value['metrics'])
        return value

    def action(self, action):
        with self.connection() as c:
            value = self.decode_action(c.execute('SELECT * FROM actions WHERE id=?',(action,)).fetchone())
            if value is None:
                raise KeyError('Действие не найдено')
            return value

    def request(self,campaign,request_id):
        with self.connection() as c:
            return self.decode_action(c.execute('SELECT * FROM actions WHERE campaign=? AND request_id=?',(campaign,request_id)).fetchone())

    def stage(self,campaign,request_id,original,plan,version,metrics):
        action = uid()
        with self.connection(True) as c:
            if self.campaign(campaign,c)['version'] != version:
                raise ValueError('Сцена изменилась. Разберите действие заново.')
            existing = c.execute('SELECT * FROM actions WHERE campaign=? AND request_id=?',(campaign,request_id)).fetchone()
            if existing:
                return self.decode_action(existing)
            c.execute('INSERT INTO actions(id,campaign,request_id,original,plan,status,version,metrics,created) VALUES(?,?,?,?,?,?,?,?,?)',
                      (action,campaign,request_id,original,plan.model_dump_json(),'pending',version,dump(metrics),time.time()))
        return self.action(action)

    def edit(self,action,plan):
        with self.connection(True) as c:
            row = self.decode_action(c.execute('SELECT * FROM actions WHERE id=?',(action,)).fetchone())
            if not row:
                raise KeyError('Действие не найдено')
            if row['status'] != 'pending':
                raise ValueError('Редактировать можно только неподтверждённый план')
            c.execute('UPDATE actions SET plan=? WHERE id=?',(plan.model_dump_json(),action))
        return self.action(action)

    def cancel(self,action):
        with self.connection(True) as c:
            if c.execute("UPDATE actions SET status='cancelled' WHERE id=? AND status='pending'",(action,)).rowcount != 1:
                raise ValueError('Можно отменить только неподтверждённое действие')

    def apply(self,action,rng=None):
        database_start=time.perf_counter()
        with self.connection(True) as c:
            row = self.decode_action(c.execute('SELECT * FROM actions WHERE id=?',(action,)).fetchone())
            if not row:
                raise KeyError('Действие не найдено')
            if row['status'] == 'applied':
                return self.event_for_action(action,c)
            if row['status'] != 'pending':
                raise ValueError('Действие отменено или уже откачено')
            campaign = self.campaign(row['campaign'],c)
            if campaign['version'] != row['version']:
                raise ValueError('Этот план устарел: состояние изменилось. Отмените и повторите ввод.')
            state = campaign['state']
            if state.get('adventure',{}).get('pending_hazard'):raise ValueError('Последствие экспедиции ожидает разбора; доступен откат или восстановление сохранения')
            before = dump(state)
            # Where the clock stood when this turn began. Calendar boundaries
            # crossed by ANY step of the turn are scored before the commit.
            clock_before = int(state.get('elapsed_minutes', 0) or 0)
            plan = Plan.model_validate(row['plan'])
            started = time.perf_counter()
            if row['metrics'].get('natural'):
                from .natural import resolve
                results,used=resolve(state,plan,rng)
                c.execute('UPDATE actions SET plan=? WHERE id=?',(used.model_dump_json(),action))
            else:
                from .natural import resolve
                results,_=resolve(state,plan,rng,scenario_checks=False,resume=False)
            from .adventure_engine import after_transition
            if not any(r.get('status')=='critical_unsupported' for r in results):after_transition(state,results,rng=rng,before=json.loads(before))
            from .world_service import _recover_calendar
            _recover_calendar(state,c,row['campaign'],clock_before)
            event = uid()
            metrics = row['metrics']
            from .rulesets import active
            preset=active(state);metrics['ruleset']={'id':preset.id,'version':preset.version}
            metrics['rules_ms'] = round((time.perf_counter()-started)*1000,3)
            c.execute('INSERT INTO events(id,action,campaign,before_state,after_state,results,created) VALUES(?,?,?,?,?,?,?)',
                      (event,action,row['campaign'],before,dump(state),dump(results),time.time()))
            c.execute('UPDATE campaigns SET state=?,version=version+1 WHERE id=?',(dump(state),row['campaign']))
            c.execute("UPDATE actions SET status='applied',metrics=? WHERE id=?",(dump(metrics),action))
            memory_started=time.perf_counter()
            self.memory.ingest(c,event)
            metrics['memory_update_ms']=round((time.perf_counter()-memory_started)*1000,3)
            if metrics.get('natural'):
                c.execute("UPDATE conversations SET status='resolved' WHERE campaign=? AND kind='clarification' AND status='open'",(row['campaign'],))
        metrics['database_ms']=round((time.perf_counter()-database_start)*1000-metrics['rules_ms'],3)
        with self.connection(True) as c:
            c.execute('UPDATE actions SET metrics=? WHERE id=?',(dump(metrics),action))
        return self.event(event)

    @staticmethod
    def decode_event(row):
        if not row:
            raise KeyError('Событие не найдено')
        value = dict(row)
        for key in ('before_state','after_state','results'):
            value[key] = json.loads(value[key])
        return value

    def event(self,event):
        with self.connection() as c:
            return self.decode_event(c.execute('SELECT * FROM events WHERE id=?',(event,)).fetchone())

    def event_for_action(self,action,c=None):
        if c is None:
            with self.connection() as db:
                return self.event_for_action(action,db)
        return self.decode_event(c.execute('SELECT * FROM events WHERE action=?',(action,)).fetchone())

    def history(self,campaign,limit=50):
        with self.connection() as c:
            rows = c.execute('''SELECT e.id,e.seq,e.results,e.narrative,e.narrative_status,e.error,e.undone,e.created,e.narrative_data,
                      a.original,a.metrics,a.request_id FROM events e JOIN actions a ON a.id=e.action
                      WHERE e.campaign=? ORDER BY seq DESC LIMIT ?''',(campaign,limit)).fetchall()
            history=[dict(dict(r),results=json.loads(r['results']),metrics=json.loads(r['metrics']),narrative_data=json.loads(r['narrative_data'])) for r in reversed(rows)]
            for item in history:
                from .world import public_results
                # Rows written before the filter existed are replayed on every
                # open, so the read side cleans too: storage is not trusted.
                if item.get('narrative'):
                    from .game import player_text
                    item['narrative']=player_text(item['narrative'])
                item['results']=public_results(item['results'])
                for proposal in item['narrative_data'].get('proposals',[]):
                    if proposal.get('confirmed_event'):
                        active=c.execute('SELECT undone FROM events WHERE id=?',(proposal['confirmed_event'],)).fetchone()
                        if not active or active['undone']:proposal['status']='pending'
            return history

    def context_history(self,campaign,before_seq,limit=2):
        with self.connection() as c:
            return [dict(r) for r in reversed(c.execute('''SELECT a.original FROM events e JOIN actions a ON a.id=e.action
                WHERE e.campaign=? AND e.seq<? AND e.undone=0 AND e.narrative_status='complete' AND e.error=''
                ORDER BY e.seq DESC LIMIT ?''',(campaign,before_seq,limit)).fetchall())]

    def pending(self,campaign):
        with self.connection() as c:
            return [self.decode_action(r) for r in c.execute("SELECT * FROM actions WHERE campaign=? AND status='pending' ORDER BY created DESC LIMIT 10",(campaign,))]

    def narrative(self,event,text,status,error='',metrics=None,structure=None):
        with self.connection(True) as c:
            row = c.execute('SELECT * FROM events WHERE id=? AND undone=0',(event,)).fetchone()
            if not row:
                raise ValueError('Событие откачено или не существует')
            if status=='streaming' and row['narrative']:
                c.execute('INSERT INTO narrative_versions VALUES(?,?,?,?,?)',(uid(),event,row['narrative'],'',time.time()))
            c.execute('UPDATE events SET narrative=?,narrative_status=?,error=? WHERE id=?',(text,status,error,event))
            if status=='streaming':c.execute("UPDATE events SET narrative_data='{}' WHERE id=?",(event,))
            if structure is not None:
                from .narration import Narrative
                data=Narrative.model_validate(structure).model_dump()
                data['presentation_only']=True
                for proposal in data['proposals']:
                    proposal.update(id=uid(),status='pending')
                c.execute('UPDATE events SET narrative_data=? WHERE id=?',(dump(data),event))
            c.execute('DELETE FROM journal_fts WHERE event_id=?',(event,))
            if status=='complete':
                c.execute('INSERT INTO journal_fts VALUES(?,?,?)',(event,row['campaign'],text))
            if metrics:
                action = self.decode_action(c.execute('SELECT * FROM actions WHERE id=?',(row['action'],)).fetchone())
                merged = action['metrics']
                merged.setdefault('llm',[]).extend(metrics)
                c.execute('UPDATE actions SET metrics=? WHERE id=?',(dump(merged),row['action']))

    def edit_narrative(self,event,text):
        with self.connection(True) as c:
            row=c.execute('SELECT * FROM events WHERE id=? AND undone=0',(event,)).fetchone()
            if not row:raise KeyError('Событие не найдено или отменено')
            if row['narrative_status']=='streaming':raise ValueError('Сначала остановите генерацию')
            c.execute('INSERT INTO narrative_versions VALUES(?,?,?,?,?)',(uid(),event,row['narrative'],text,time.time()))
            c.execute("UPDATE events SET narrative=?,narrative_status='complete',error='' WHERE id=?",(text,event))
            c.execute('DELETE FROM journal_fts WHERE event_id=?',(event,))
            c.execute('INSERT INTO journal_fts VALUES(?,?,?)',(event,row['campaign'],text))
        return self.event(event)

    def decide_proposal(self,event,proposal_id,approve):
        with self.connection(True) as c:
            row=c.execute('SELECT * FROM events WHERE id=? AND undone=0',(event,)).fetchone()
            if not row:raise KeyError('Исходный ход не найден или отменён')
            if row['narrative_status']!='complete':raise ValueError('Дождитесь завершённого художественного ответа')
            data=json.loads(row['narrative_data'])
            proposal=next((p for p in data.get('proposals',[]) if p['id']==proposal_id),None)
            if not proposal:raise KeyError('Предложение не найдено; возможно, описание перегенерировано')
            if proposal.get('confirmed_event'):
                existing=c.execute('SELECT * FROM events WHERE id=?',(proposal['confirmed_event'],)).fetchone()
                if existing and not existing['undone']:return self.decode_event(existing)
            if proposal['status']=='rejected':raise ValueError('Предложение уже отклонено')
            if not approve:
                proposal['status']='rejected';c.execute('UPDATE events SET narrative_data=? WHERE id=?',(dump(data),event));return None
            campaign=self.campaign(row['campaign'],c);state=campaign['state'];before=dump(state)
            # Утверждается только описательное сведение: хиты, вещи и смерть не изменяются.
            state['facts'].append(proposal['text']);state['turn']+=1
            action,event_id=uid(),uid()
            plan=Plan(summary='Утверждение нового сведения',steps=[dict(kind='observe',text=proposal['text'])])
            c.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)',(action,row['campaign'],'fact:'+uid(),
                'Подтверждаю сведение: '+proposal['text'],plan.model_dump_json(),'applied',campaign['version'],dump({'route':'A','llm':[]}),time.time()))
            c.execute('INSERT INTO events(id,action,campaign,before_state,after_state,results,narrative,narrative_status,created) VALUES(?,?,?,?,?,?,?,?,?)',
                (event_id,action,row['campaign'],before,dump(state),dump([{'type':'canon','text':proposal['text'],'source':proposal_id}]),
                 'Подтверждённое сведение: '+proposal['text'],'complete',time.time()))
            c.execute('UPDATE campaigns SET state=?,version=version+1 WHERE id=?',(dump(state),row['campaign']))
            c.execute("UPDATE actions SET status='stale' WHERE campaign=? AND status='pending'",(row['campaign'],))
            proposal.update(status='approved',confirmed_event=event_id)
            c.execute('UPDATE events SET narrative_data=? WHERE id=?',(dump(data),event))
            self.memory.ingest(c,event_id)
        return self.event(event_id)

    def profiles(self):
        with self.connection() as c:
            return [dict(id=r['id'],name=r['name'],master=json.loads(r['master'])) for r in c.execute('SELECT * FROM master_profiles ORDER BY created')]

    def record_scenario(self,campaign,request_id,summary,transition):
        """Trusted Python scenario/rules hook. Not exposed to UI or LLM; uses the same journal.

        transition mutates the canonical state and returns structured confirmed results.
        It is responsible for authorizing its effects. This is not an inventory/combat engine.
        """
        if not callable(transition):raise ValueError('Scenario transition must be trusted program code')
        with self.connection(True) as c:
            return self.engine_event(c,self.campaign(campaign,c),request_id,summary,transition)

    def engine_event(self,c,current,request_id,summary,transition,signature=None):
        """Registered program engines share the existing atomic event/state/ACL transaction."""
        campaign=current['id']
        old=c.execute('SELECT id,metrics FROM actions WHERE campaign=? AND request_id=?',(campaign,request_id)).fetchone()
        if old:
            event=self.event_for_action(old['id'],c)
            if event['undone']:raise ValueError('Scenario event belongs to an abandoned branch')
            if signature and json.loads(old['metrics']).get('engine_signature')!=signature:raise ValueError('request_id уже использован для другой команды')
            return event
        state=current['state']
        if state.get('adventure',{}).get('pending_hazard'):raise ValueError('Последствие экспедиции ожидает разбора; доступен откат или восстановление сохранения')
        before=dump(state)
        clock_before=int(state.get('elapsed_minutes',0) or 0)
        results=transition(state)
        # Every engine turn that moved the clock owes the boundaries it crossed,
        # whatever route produced it (a combat round, a forage, a rest).
        from .world_service import _recover_calendar
        _recover_calendar(state,c,campaign,clock_before)
        if not isinstance(results,list) or not all(isinstance(r,dict) and r.get('type')=='scripted' for r in results):raise ValueError('Scenario must supply structured scripted results')
        if state.get('world',{}).get('events'):
            from .world import tick
            messages,evidence=tick(state)
            if results:
                results[-1].setdefault('world_events',[]).extend(messages)
                results[-1].setdefault('memory_evidence',[]).extend(evidence)
        from .adventure_engine import after_transition
        after_transition(state,results,before=json.loads(before))
        from .rulesets import active
        preset=active(state)
        state['turn']=state.get('turn',0)+1
        # A turn always produces an action record. An opening turn whose material
        # never named a place has no neutral summary, so name the step itself.
        summary=str(summary or '').strip() or 'Ход героя'
        action,event=uid(),uid();plan=Plan(summary=summary,steps=[dict(kind='other',text=summary)])
        metrics={'route':'scenario','llm':[],'engine_signature':signature,'ruleset':{'id':preset.id,'version':preset.version}}
        c.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)',(action,campaign,request_id,summary,plan.model_dump_json(),'applied',current['version'],dump(metrics),time.time()))
        c.execute('INSERT INTO events(id,action,campaign,before_state,after_state,results,created) VALUES(?,?,?,?,?,?,?)',(event,action,campaign,before,dump(state),dump(results),time.time()))
        c.execute('UPDATE campaigns SET state=?,version=version+1 WHERE id=?',(dump(state),campaign));self.memory.ingest(c,event)
        return self.event_for_action(action,c)

    def create_profile(self,name,master):
        profile=uid()
        with self.connection(True) as c:
            c.execute('INSERT INTO master_profiles VALUES(?,?,?,?)',(profile,name,master.model_dump_json(),time.time()))
        return profile

    def change_profile(self,profile,name=None,master=None,*,delete=False):
        with self.connection(True) as c:
            row=c.execute('SELECT * FROM master_profiles WHERE id=?',(profile,)).fetchone()
            if not row:raise KeyError('Профиль не найден')
            if delete and profile=='personal-gm-v1.1':raise ValueError('Встроенный профиль сохраняется; можно создать его копию')
            c.execute('INSERT INTO profile_versions VALUES(?,?,?,?,?,?)',
                (uid(),profile,row['name'],row['master'],'delete' if delete else 'edit',time.time()))
            if delete:c.execute('DELETE FROM master_profiles WHERE id=?',(profile,))
            else:c.execute('UPDATE master_profiles SET name=?,master=? WHERE id=?',(name,master.model_dump_json(),profile))

    def profile_history(self,profile):
        with self.connection() as c:
            return [dict(id=r['id'],name=r['name'],master=json.loads(r['master']),operation=r['operation'],created=r['created'])
                    for r in c.execute('SELECT * FROM profile_versions WHERE profile=? ORDER BY created DESC LIMIT 50',(profile,))]

    def gm_stage(self,campaign,request_id,question):
        with self.connection(True) as c:
            self.campaign(campaign,c)
            old=c.execute('SELECT * FROM gm_messages WHERE campaign=? AND request_id=?',(campaign,request_id)).fetchone()
            if old:
                if old['question']!=question:raise ValueError('request_id уже относится к другому вопросу')
                return dict(old)
            message=uid();c.execute('INSERT INTO gm_messages(id,campaign,request_id,question,created) VALUES(?,?,?,?,?)',
                                   (message,campaign,request_id,question,time.time()))
        return self.gm_message(message)

    def gm_message(self,message):
        with self.connection() as c:
            row=c.execute('SELECT * FROM gm_messages WHERE id=?',(message,)).fetchone()
            if not row:raise KeyError('Обращение не найдено')
            return dict(row)

    def gm_request(self,campaign,request_id):
        with self.connection() as c:
            row=c.execute('SELECT * FROM gm_messages WHERE campaign=? AND request_id=?',(campaign,request_id)).fetchone()
            return dict(row) if row else None

    def conversation(self,campaign,request_id,db=None):
        # db lets a caller inside a write transaction read the row it just wrote
        # instead of opening a second connection to a locked database.
        if db is not None:
            row=db.execute('SELECT * FROM conversations WHERE campaign=? AND request_id=?',(campaign,request_id)).fetchone()
            return dict(row) if row else None
        with self.connection() as c:
            row=c.execute('SELECT * FROM conversations WHERE campaign=? AND request_id=?',(campaign,request_id)).fetchone()
            return dict(row) if row else None

    def converse(self,campaign,request_id,original,reply,kind='clarification',context=None,db=None):
        """Record one player line and the master's answer as a single row.

        A caller that already holds a write transaction must pass its own
        connection as db. Opening a second one takes another BEGIN IMMEDIATE,
        SQLite refuses it while the first is open, and the caller then reports a
        technical lock as if the scene had moved. With db the row joins the
        caller's transaction and rolls back with it, so a turn can never leave
        its event without its dialogue or the reverse.
        """
        if db is not None:
            self._converse_in(db,campaign,request_id,original,reply,kind,context)
            return self.conversation(campaign,request_id,db)
        with self.connection(True) as c:
            self._converse_in(c,campaign,request_id,original,reply,kind,context)
        return self.conversation(campaign,request_id)

    def _converse_in(self,c,campaign,request_id,original,reply,kind,context):
        self.campaign(campaign,c)
        old=c.execute('SELECT * FROM conversations WHERE campaign=? AND request_id=?',(campaign,request_id)).fetchone()
        if old:
            if old['original']!=original:raise ValueError('Повтор запроса содержит другой текст')
            return dict(old)
        message_id=uid()
        if kind=='clarification':c.execute("UPDATE conversations SET status='superseded' WHERE campaign=? AND kind='clarification' AND status='open'",(campaign,))
        c.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?,?,?,?)',
            (message_id,campaign,request_id,original,reply,kind,dump(context or {}),'open',time.time()))
        return dict(c.execute('SELECT * FROM conversations WHERE campaign=? AND request_id=?',(campaign,request_id)).fetchone())

    def conversation_history(self,campaign,limit=50,before=None):
        """One page of dialogue, oldest first, addressed by a stable cursor.

        This used to be a hard LIMIT 50, so a campaign with 56 stored rows
        silently lost its first six and the player could never read them: the
        data was there, the reader had no way to ask for it. 'created' is a
        float that two rows can share, so the cursor is the pair (created, id):
        it names one row exactly, never shifts as new turns are added, and
        pages that meet at a boundary neither repeat nor skip a row.
        """
        with self.connection() as c:
            # request_id and context are part of the record's identity: a replay is
            # matched by request_id, and the context names the journal event this
            # row restates. Without them the reader cannot tell one turn from two.
            columns='SELECT id,request_id,original,reply,kind,context,created FROM conversations WHERE campaign=?'
            if before is None:
                rows=c.execute(columns+' ORDER BY created DESC,id DESC LIMIT ?',(campaign,limit)).fetchall()
            else:
                cut_created,cut_id=before
                # Strictly older than the cursor row, in the same total order.
                rows=c.execute(columns+' AND (created<? OR (created=? AND id<?)) ORDER BY created DESC,id DESC LIMIT ?',
                               (campaign,cut_created,cut_created,cut_id,limit)).fetchall()
            return [dict(r) for r in reversed(rows)]

    def conversation_cursor(self,rows):
        """The cursor that asks for the page before these rows."""
        if not rows:return None
        first=rows[0]
        return [first['created'],first['id']]

    def conversation_count(self,campaign):
        with self.connection() as c:
            return c.execute('SELECT COUNT(*) FROM conversations WHERE campaign=?',(campaign,)).fetchone()[0]

    def open_question(self,campaign):
        with self.connection() as c:
            row=c.execute("SELECT * FROM conversations WHERE campaign=? AND kind='clarification' AND status='open' ORDER BY created DESC LIMIT 1",(campaign,)).fetchone()
            return dict(row) if row else None

    def finish_question(self,question,status='resolved'):
        with self.connection(True) as c:c.execute('UPDATE conversations SET status=? WHERE id=?',(status,question))

    def gm_finish(self,message,answer,status,metrics):
        with self.connection(True) as c:
            c.execute('UPDATE gm_messages SET answer=?,status=?,metrics=? WHERE id=?',(answer,status,dump(metrics),message))

    def gm_history(self,campaign):
        with self.connection() as c:
            rows=c.execute('SELECT * FROM gm_messages WHERE campaign=? ORDER BY created DESC LIMIT 50',(campaign,)).fetchall()
            return [dict(dict(r),metrics=json.loads(r['metrics'])) for r in reversed(rows)]

    def undo(self,campaign):
        with self.connection(True) as c:
            previous=self.campaign(campaign,c)['state']
            row = c.execute('SELECT * FROM events WHERE campaign=? AND undone=0 ORDER BY seq DESC LIMIT 1',(campaign,)).fetchone()
            if not row:
                raise ValueError('Нет хода для отмены')
            c.execute('UPDATE campaigns SET state=?,version=version+1 WHERE id=?',(row['before_state'],campaign))
            c.execute('UPDATE events SET undone=1 WHERE id=?',(row['id'],))
            c.execute("UPDATE actions SET status='undone' WHERE id=?",(row['action'],))
            c.execute("UPDATE actions SET status='stale' WHERE campaign=? AND status='pending'",(campaign,))
            c.execute('DELETE FROM journal_fts WHERE event_id=?',(row['id'],))
            c.execute('INSERT INTO state_operations VALUES(?,?,?,?,?,?,?)',
                      (uid(),campaign,'undo',dump(previous),row['before_state'],row['id'],time.time()))
        return self.campaign(campaign)

    def save(self,campaign,name):
        with self.connection(True) as c:
            state = self.campaign(campaign,c)['state']
            seq = c.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE campaign=?',(campaign,)).fetchone()[0]
            checkpoint = uid()
            active=[r[0] for r in c.execute('SELECT id FROM events WHERE campaign=? AND undone=0',(campaign,))]
            c.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)',(checkpoint,campaign,name,dump(state),seq,time.time(),dump(active)))
        self.backup()
        return checkpoint

    def saves(self,campaign):
        with self.connection() as c:
            return [dict(r) for r in c.execute('SELECT id,name,created FROM checkpoints WHERE campaign=? ORDER BY created DESC',(campaign,))]

    def restore(self,campaign,checkpoint):
        with self.connection(True) as c:
            previous=self.campaign(campaign,c)['state']
            row = c.execute('SELECT * FROM checkpoints WHERE id=? AND campaign=?',(checkpoint,campaign)).fetchone()
            if not row:
                raise KeyError('Сохранение не найдено')
            c.execute('UPDATE campaigns SET state=?,version=version+1 WHERE id=?',(row['state'],campaign))
            # После загрузки контрольной точки история будущего не используется в контексте.
            active=set(json.loads(row['active']))
            ids = c.execute('SELECT id,action,narrative,narrative_status FROM events WHERE campaign=?',(campaign,)).fetchall()
            for event in ids:
                undone=event['id'] not in active
                c.execute('UPDATE events SET undone=? WHERE id=?',(int(undone),event['id']))
                c.execute('UPDATE actions SET status=? WHERE id=?',('undone' if undone else 'applied',event['action']))
                c.execute('DELETE FROM journal_fts WHERE event_id=?',(event['id'],))
                if not undone and event['narrative_status']=='complete':
                    c.execute('INSERT INTO journal_fts VALUES(?,?,?)',(event['id'],campaign,event['narrative']))
            c.execute("UPDATE actions SET status='stale' WHERE campaign=? AND status='pending'",(campaign,))
            c.execute('INSERT INTO state_operations VALUES(?,?,?,?,?,?,?)',
                      (uid(),campaign,'restore',dump(previous),row['state'],checkpoint,time.time()))
        return self.campaign(campaign)

    def master(self,campaign,master):
        with self.connection(True) as c:
            old = self.campaign(campaign,c)['master']
            c.execute('INSERT INTO master_versions(campaign,master,created) VALUES(?,?,?)',(campaign,dump(old),time.time()))
            c.execute('UPDATE campaigns SET master=? WHERE id=?',(master.model_dump_json(),campaign))

    def master_history(self,campaign):
        with self.connection() as c:
            return [dict(id=r['id'],created=r['created'],master=json.loads(r['master'])) for r in c.execute('SELECT * FROM master_versions WHERE campaign=? ORDER BY id DESC LIMIT 20',(campaign,))]

    def backup(self):
        target = self.path.parent/'backups'
        target.mkdir(exist_ok=True)
        # Фиксированный последний backup — не бесконечный рост диска.
        with self.connection() as source, closing(sqlite3.connect(target/'latest.sqlite3')) as dest:
            with dest:
                source.backup(dest)
        return target/'latest.sqlite3'