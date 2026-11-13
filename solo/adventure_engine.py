"""Reviewed scenario effects run inside the existing state/event transaction."""
import copy,hashlib,json,re,secrets
from .adventure_contracts import AdventureDefinition

UNKNOWN=object()

def random_source(rng=None):
    if rng is None:return secrets.SystemRandom()
    if hasattr(rng,'randint'):return rng
    if not callable(rng):raise ValueError('Источник случайности должен быть программным')
    class Adapter:
        def randint(self,low,high):
            value=rng(high-low+1)
            if not isinstance(value,int) or isinstance(value,bool) or not 0<=value<=high-low:raise ValueError('Источник случайности вернул недопустимый результат')
            return low+value
    return Adapter()

def value_at(state,path):
    value=state
    for key in path.split('.'):
        if not isinstance(value,dict) or key not in value:return UNKNOWN
        value=value[key]
    return value

def satisfied(state,predicate):
    value=value_at(state,predicate.path)
    if value is UNKNOWN:return False
    target=predicate.value
    try:
        if predicate.op=='exists':return value is not None
        if predicate.op=='eq':return value==target
        if predicate.op=='ne':return value!=target
        if predicate.op=='gte':return value>=target
        if predicate.op=='lte':return value<=target
        return target in value
    except TypeError:return False

def roll(formula,rng=None):
    match=re.fullmatch(r'(\d+)d(4|6|8|10|12|20|100)([+-]\d+)?',formula)
    if not match or not 1<=int(match[1])<=20:raise ValueError('Неподдержанная формула кубиков')
    count,sides,modifier=int(match[1]),int(match[2]),int(match[3] or 0);dice=[]
    for _ in range(count):
        value=random_source(rng).randint(1,sides)
        if not isinstance(value,int) or isinstance(value,bool) or not 1<=value<=sides:raise ValueError('Источник случайности вернул недопустимый результат')
        dice.append(value)
    return dict(formula=formula,dice=dice,modifier=modifier,total=sum(dice)+modifier)

def initialize(state,bundle,definition,library=None,*,standalone=False):
    from .rulesets import active
    definition=AdventureDefinition.model_validate(definition) if isinstance(definition,dict) else definition
    preset=active(state)
    if not definition.reviewed or any(x.source.review!='confirmed' for x in [*definition.events,*definition.tables,*definition.trackers,*definition.survival,*definition.companions,*definition.wilderness]):raise ValueError('Сценарные инструкции не проверены')
    if definition.unsupported:raise ValueError('В приключении остаются неподдержанные механики. Сверьте подробности в закрытой подготовке.')
    # Only the mechanics preset gates execution. The edition the book was written
    # for is a separate fact: a 2014 adventure is expected to run on the 2024
    # preset through the mapping layer, so a mismatch there is reported by the
    # mapping report and never blocks the launch on its own.
    if (preset.id,preset.version)!=(definition.ruleset,definition.ruleset_version):raise ValueError('Редакция/версия сценария не соответствует активному Ruleset')
    completed=state.get('adventure',{}).get('completed_modules',[])
    if definition.continuation_of and not all(id in completed for id in definition.continuation_of):
        if not standalone or not definition.alternate_start:raise ValueError('Не подтверждено предыдущее приключение; самостоятельный старт не выбран или не предусмотрен')
    if not all(satisfied(state,c) for c in definition.start_conditions) and not (standalone and definition.alternate_start):raise ValueError('Исходные условия приключения не подтверждены')
    if standalone and not definition.alternate_start:raise ValueError('Самостоятельный старт не предусмотрен источником')
    from .adventure_expedition import validate_rules
    validate_rules(state,definition)
    level=state['hero']['level']
    if definition.minimum_level and level<definition.minimum_level or definition.maximum_level and level>definition.maximum_level:raise ValueError('Уровень героя не соответствует проверенным условиям модуля')
    materials={}
    if library:
        if not library.get(bundle)['import_complete']:raise ValueError('Материалы комплекта ещё не извлечены или требуют проверки')
        if definition.entry_stage:
            from .adventure_library import digest
            import hashlib
            # Any prepared entry is re-checked the same way: the cited document
            # must still be the one on disk, the page must exist, and the printed
            # opening must still hash to what the reviewer confirmed. No book is
            # privileged here; Hoard is only one document that passes.
            stage=definition.entry_stage
            place=next((p for p in definition.world.locations if p.name==state['location']),None) if definition.world else None
            verified=digest(library.blob(stage.document)) if library else ''
            if verified!=stage.document or not place or hashlib.sha256(place.description.encode()).hexdigest()!=stage.opening_sha256:
                raise ValueError('Происхождение стартовой сцены не прошло повторную проверку')
        for id in definition.media:
            raw=library.material(id)
            materials[id]=dict(id=id,name=raw['public_name'],kind=raw['kind'],scope=raw['scope'],reviewed=bool(raw['reviewed']),sanitized=bool(raw['sanitized']),geometry_ready=bool(raw['geometry']),public_text=raw.get('public_text',''),known_by=['hero'])
    state['adventure']=dict(bundle=bundle,module=definition.id,title=definition.title,mode=definition.mode,
        definition=definition.model_dump(),flags={},trackers={t.id:t.value for t in definition.trackers},events={e.id:'pending' for e in definition.events},schedule={},journal=[],known_media=[],media_records=materials,media_bundles={id:bundle for id in materials},knowledge={},completed_modules=[],volumes={},started_at=state.get('elapsed_minutes',0),last_time=state.get('elapsed_minutes',0),companions={},weather=None)
    if standalone:state['adventure']['flags'].update(definition.alternate_flags)
    state['adventure']['companions']={c.id:c.model_dump() for c in definition.companions}
    return state['adventure']

def projection(state):
    data=state.get('adventure')
    if not data:return None
    definition=AdventureDefinition.model_validate(data['definition'])
    media=[{k:v for k,v in data.get('media_records',{}).get(id,{}).items() if k in ('id','name','kind','geometry_ready','public_text','known_by')} for id in data.get('known_media',[]) if data.get('media_records',{}).get(id,{}).get('scope')!='gm']
    trackers=[dict(id=t.id,name=t.name,value=data['trackers'].get(t.id,t.value),minimum=t.minimum,maximum=t.maximum) for t in definition.trackers if t.public]
    from .adventure_expedition import assess_solo
    return dict(title=data['title'],mode=data['mode'],materials=media,trackers=trackers,entry_readiness=dict(scope='entry',boundary=definition.entry_stage.boundary) if definition.entry_stage else None,
                evidence=[dict(id=id,text=raw['text'],known_by=['hero'],truth='confirmed' if raw.get('truth','confirmed')=='confirmed' else 'rumor') for id,raw in data.get('knowledge',{}).items() if raw.get('public')],elapsed_minutes=state.get('elapsed_minutes',0),weather=data.get('weather') if (data.get('weather') or {}).get('public') else None,companions=[dict(id=c['id'],name=c['npc'],status=c['status']) for c in data.get('companions',{}).values() if c['status'] in ('joined','left')],solo=assess_solo(state,definition),hazard_pending=bool(data.get('pending_hazard')))

def reveal(state,material,bundle):
    """A confirmed map or handout the table has already laid out.

    Same gate as the engine's own `handout` effect: a Master's map, a material
    nobody confirmed and an unsanitized copy never reach the players. The only
    difference is who asked — here the player did, in words. Returns True when
    the material is new to `known_media`, so a repeated phrase books nothing.
    """
    data=state.get('adventure')
    if not data:raise ValueError('К кампании не подключён проверенный сценарий: раздатку показывать некуда')
    id=material.get('id')
    if not id or material.get('status')!='confirmed' or not material.get('reviewed') or not material.get('sanitized') or material.get('scope')=='gm':
        raise ValueError('Материал не подтверждён для игроков')
    name=material.get('public_name') or material.get('caption') or ''
    data.setdefault('media_records',{})[id]=dict(id=id,name=name,kind=material.get('kind','handout'),scope=material.get('scope','player'),reviewed=True,sanitized=True,geometry_ready=bool(material.get('geometry')),public_text=material.get('public_text','') or '',known_by=['hero'])
    data.setdefault('media_bundles',{})[id]=bundle
    known=data.setdefault('known_media',[])
    if id in known:return False
    known.append(id)
    data.setdefault('journal',[]).append(dict(id='handout-'+id,category='handout',status='resolved',game_time=state.get('elapsed_minutes',0),due=state.get('elapsed_minutes',0),rolls=[],source=dict(document=material.get('document',''),page=material.get('page'),review='confirmed'),effects=[dict(kind='handout',target=id)]))
    return True

class NeedsScreen(ValueError):
    """A pending effect the Nat20 screen still cannot resolve from its state.

    Raised on the way out of the queue, never inside a turn that merely records
    the entry: the entry stays pending and the reason is printed to the table,
    so nothing is dropped and nothing is guessed.
    """


def _pending_key(item):
    """Stable identity of one pending effect: no ordinal, no random id."""
    return '|'.join(str(item.get(key) or '') for key in ('event','kind','target','text'))


def _waiting(raw):
    """What the table reads while an effect waits for the Nat20 screen."""
    phrase='Сценарный эффект «'+raw.kind+'»'+((' «'+raw.text+'»') if raw.text else '')
    tail={'check':' Проверка будет брошена на ближайшем ходе Nat20.',
          'table':' Таблица будет брошена на ближайшем ходе Nat20.',
          'resource':' Счётчик изменится на ближайшем ходе Nat20.',
          'reward':' Награда будет начислена на ближайшем ходе Nat20.'}
    return phrase+' ожидает игрового экрана Nat20.'+tail.get(raw.kind,'')


class _Settled(Exception):
    """The entry was already executed on an earlier turn: a repeat, not news."""


def _pending_effect(record,item):
    """The declared effect behind one pending entry, and its declaration slot.

    The queue keeps only event/kind/target/text/page, so the ability, the DC
    and the table id are recovered from the event's own journal record: the
    throw the screen makes is the one the book declared, not a re-guess. The
    slot index is the entry's identity, which keeps two checks that differ only
    in their DC apart and stops a settled entry from paying out twice.
    """
    from .adventure_contracts import Effect
    if record is None:raise NeedsScreen('событие «'+str(item.get('event'))+'» не найдено в журнале')
    settled=set(record.get('settled') or [])
    matched=False
    for index,raw in enumerate(record.get('effects',[])):
        if (raw.get('kind')==item.get('kind') and raw.get('target','')==item.get('target','')
                and raw.get('text','')==item.get('text','')):
            matched=True
            if index in settled:continue
            return Effect.model_validate(raw),index
    if matched:raise _Settled()
    raise NeedsScreen('в событии «'+str(item.get('event'))+'» нет такого эффекта')


def _nat20_check(state,raw,rng,notices,record):
    """A printed check rolled by the Nat20 engine, with its numbers spoken."""
    from .check_text import check_text
    from .nat20_checks import resolve_state_check
    from .nat20_worker import plain
    if raw.ability is None or raw.dc is None:
        raise NeedsScreen('проверка без подтверждённых характеристики и Сл')
    runtime=state.get('nat20')
    if not runtime:raise NeedsScreen('в кампании нет состояния Nat20')
    result=plain(resolve_state_check(runtime,kind='ability',skill=None,ability=raw.ability,dc=raw.dc,
        advantage=False,disadvantage=False,rng=random_source(rng)))
    record['rolls'].append(dict(check=result,source=record.get('source') or {},engine='nat20'))
    state['adventure']['flags'][raw.target]=result['success']
    page=(record.get('source') or {}).get('page')
    notices.append(dict(kind='check',text=check_text(result)+(' (стр. '+str(page)+')' if page else '')))
    return result


def _nat20_resource(state,raw):
    """One hero counter written in the durable state Nat20 itself owns.

    The legacy hero keeps a ``resources`` map; a Nat20 campaign keeps hit dice
    and purse gold on its own runtime, and the hero sheet is re-projected from
    it on every turn, so a host-side write into ``state['hero']`` would vanish.
    Only named counters are touched, all with the legacy meaning of the value
    (a delta of the current amount): anything else stays pending with the
    reason instead of inventing a field.
    """
    runtime=state.get('nat20')
    if not runtime:raise NeedsScreen('в кампании нет состояния Nat20')
    if not isinstance(raw.value,int) or isinstance(raw.value,bool):
        raise NeedsScreen('ресурсу нужен целый модификатор')
    if raw.target=='hit_dice':
        pool=runtime['hit_dice']
        pool['dice_remaining']=max(0,min(pool['dice_total'],pool['dice_remaining']+raw.value))
        return 'Кости хитов: '+str(pool['dice_remaining'])
    if raw.target=='wealth_gp':
        sheet=runtime['sheet'];sheet['wealth_gp']=max(0,int(sheet.get('wealth_gp') or 0)+raw.value)
        return 'Золото: '+str(sheet['wealth_gp'])
    if raw.target=='experience':
        # Nat20 pays experience only through confirmed reward receipts, and its
        # durable state refuses any other difference. Naming that is honest;
        # writing the number anyway would lose the whole turn at save time.
        raise NeedsScreen('опыт Nat20 начисляется только подтверждёнными записями награды')
    raise NeedsScreen('ресурс «'+str(raw.target)+'» не заведён в состоянии Nat20')


def _book_reward(state,definition,raw,notices,record=None):
    """A printed reward booked once, with the page it came from.

    A reward the rules cannot price yet is recorded as pending, never as zero.
    The booking is the scenario's own ledger on every path, and for a Nat20
    campaign it is also a confirmed receipt in the engine's experience ledger:
    the payout is written there by ``_nat20_experience`` instead of into a
    second counter, because the durable state refuses experience its receipts
    do not sum to.
    """
    from .progression import book_progression
    data=state['adventure']
    rules={rule.id:rule for rule in (definition.progression.rewards or book_progression().rewards)}
    rule=rules.get(raw.target)
    if rule is None:raise ValueError('Награда не зарегистрирована в приключении: '+str(raw.target))
    ledger=data.setdefault('rewards',{})
    if raw.target in ledger:
        notices.append(dict(kind='reward',text='Награда уже начислена: '+raw.target))
    elif rule.pending_adaptation:
        ledger[raw.target]=dict(status='pending_adaptation',page=rule.page,reason=rule.reason)
        notices.append(dict(kind='reward',text='Награда ожидает адаптации: '+rule.reason))
    else:
        ledger[raw.target]=dict(status='applied',experience=rule.amount,page=rule.page,basis=rule.basis)
        notices.append(dict(kind='reward',text='Награда начислена: '+str(rule.amount)+' опыта (стр. '+str(rule.page)+')'))
        _nat20_experience(state,definition,rule,record,notices)


def _nat20_experience(state,definition,rule,record,notices):
    """A printed reward pays into the counter Nat20 itself owns.

    A Nat20 campaign derives its experience from confirmed reward receipts and
    refuses a durable state whose experience differs from their sum, so the
    payout is written as one more receipt of that same ledger (``kind
    ``='printed'``) and never as a second counter beside the engine's. The
    receipt id is the module plus the printed rule's id, so the same line cannot
    be paid twice while two modules that print the same rule id each pay their
    own amount, and its hash pins the declaration slot the scenario booked it in:
    a replayed queue hits the ledger first and books nothing.
    """
    runtime=state.get('nat20')
    if not runtime:return None
    amount=int(getattr(rule,'amount',None) or 0)
    if amount<=0:return None
    rewards=runtime.setdefault('rewards',[])
    receipt_id='printed:'+str(definition.id)+':'+str(rule.id)
    if any(receipt.get('id')==receipt_id for receipt in rewards):
        notices.append(dict(kind='experience',text='Опыт за награду «'+str(rule.id)
            +'» уже зачислен в журнал Nat20: второй раз не начисляется'))
        return None
    event=(record or {}).get('id') or ''
    slot=hashlib.sha256(json.dumps(dict(scenario=definition.id,event=event,reward=rule.id,
        amount=amount,page=rule.page),sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
    receipt=dict(id=receipt_id,encounter_id='',combat_id=event or 'scenario:'+definition.id,
        experience=amount,outcome_sha256=slot,kind='printed',page=rule.page,
        source=dict(kind='authored_scenario',id=definition.id,version=str(definition.version),
            note='Печатная награда «'+str(rule.id)+'» из сценария «'+definition.title+'», стр. '+str(rule.page)))
    rewards.append(receipt)
    runtime['experience']=int(runtime.get('experience') or 0)+amount
    # The legacy hero is a projection of this runtime, not a second counter: the
    # sheet shows the paid number in the same turn, and Nat20 re-derives it on
    # the next screen turn anyway.
    if isinstance(state.get('hero'),dict):state['hero']['experience']=runtime['experience']
    # Validate the write with the very contract the next screen turn will use,
    # so a broken receipt fails this effect (and stays pending) instead of
    # poisoning the campaign at save time.
    from .nat20_contracts import DurableState
    DurableState.model_validate(runtime)
    notices.append(dict(kind='experience',text='Опыт Nat20: '+str(runtime['experience'])
        +' (+'+str(amount)+', стр. '+str(rule.page)+')'))
    build=(runtime.get('definition') or {}).get('build') or {}
    from dnd5e_engine.rules.advancement import level_for_experience
    earned=level_for_experience(int(runtime['experience']))
    if earned>int(build.get('level') or 1):
        # The level is not written here: the sheet, its hit points and its
        # choices come from the existing advancement path, which this payout has
        # just made available.
        notices.append(dict(kind='advancement',text='Опыт открывает уровень '+str(earned)
            +': повышение выполняется на экране героя, сценарий уровень не повышает'))
    return receipt


def _execute(state,definition,raw,record,*,rng,tables,notices,budget,depth,mechanics,nat20):
    """One declared or pending effect, inside the transaction that owns it.

    ``mechanics=False`` records the four mechanical kinds as pending instead of
    rolling them with the legacy ruleset; ``nat20=True`` is that queue coming
    back on a screen turn, so the same declaration is now executed by Nat20.
    """
    data=state['adventure'];now=state.get('elapsed_minutes',0)
    budget[0]-=1
    if budget[0]<0 or depth>8:raise ValueError('Слишком много сценарных последствий; проверьте граф и шаг времени')
    # Mechanics of a Nat20 campaign belong to its screen. A printed check, a
    # table roll, a resource or a booked reward is not resolved here: it is
    # written down as pending, with the page it came from, so nothing is
    # silently rolled with the legacy ruleset and nothing is silently lost.
    if not mechanics and raw.kind in ('check','table','resource','reward'):
        pending=dict(event=record.get('id',''),kind=raw.kind,target=raw.target,text=raw.text,
                     page=(record.get('source') or {}).get('page'))
        data.setdefault('deferred',[]).append(pending)
        record.setdefault('deferred',[]).append(pending)
        notices.append(dict(kind='deferred',text=_waiting(raw)))
        return
    if raw.kind=='notice':
        if raw.public and raw.text:notices.append(dict(text=raw.text,kind='notice'))
    elif raw.kind=='flag':data['flags'][raw.target]=raw.value
    elif raw.kind=='reward':_book_reward(state,definition,raw,notices,record)
    elif raw.kind=='tracker':
        tracker=next(t for t in definition.trackers if t.id==raw.target)
        if not isinstance(raw.value,int) or isinstance(raw.value,bool):raise ValueError('Трекеру нужен целый модификатор')
        data['trackers'][raw.target]=max(tracker.minimum,min(tracker.maximum,data['trackers'][raw.target]+raw.value))
    elif raw.kind=='handout':
        material=data['media_records'].get(raw.target)
        if not material or material['scope']=='gm' or not material['reviewed'] or not material['sanitized']:raise ValueError('Событие не может раскрыть непроверенную карту Мастера')
        if raw.target not in data['known_media']:data['known_media'].append(raw.target)
        notices.append(dict(kind='handout',text='Обнаружен материал: '+material['name']))
    elif raw.kind=='evidence':
        data['knowledge'][raw.target]=dict(text=raw.text,public=raw.public,source=record['source'],known_by=['hero'],truth=raw.truth)
        if raw.public:notices.append(dict(kind='evidence',text=raw.text))
    elif raw.kind=='weather':
        data['weather']=dict(kind=raw.target,description=raw.text,observed_at=now,public=raw.public)
    elif raw.kind=='schedule':
        if not isinstance(raw.value,int) or raw.value<0:raise ValueError('Расписание требует неотрицательной задержки в минутах')
        data['schedule'][raw.target]=now+raw.value;data['events'][raw.target]='scheduled'
    elif raw.kind=='table':
        table=tables.get(raw.target) if nat20 else tables[raw.target]
        if table is None:raise NeedsScreen('таблица «'+str(raw.target)+'» не найдена в сценарии')
        rolled=roll(table.formula,rng);row=next(r for r in table.rows if r.low<=rolled['total']<=r.high)
        record['rolls'].append(dict(**rolled,table=table.id,row=dict(low=row.low,high=row.high,label=row.label),source=table.source.model_dump()))
        for nested in row.effects:_execute(state,definition,nested,record,rng=rng,tables=tables,notices=notices,budget=budget,depth=depth+1,mechanics=mechanics,nat20=nat20)
    elif raw.kind=='check' and nat20:_nat20_check(state,raw,rng,notices,record)
    elif raw.kind=='check':
        from .contracts import Hero,Step
        from .rulesets import RulesetEngine,active
        if raw.ability is None or raw.dc is None:raise ValueError('Сценарная проверка требует подтверждённых характеристики и Сл')
        hero=Hero.model_validate(state['hero']);step=Step(kind='check',text=raw.text or 'Сценарная проверка',ability=raw.ability,dc=raw.dc)
        result=RulesetEngine(active(state)).check(hero,step,random_source(rng));record['rolls'].append(dict(check=result,source=record['source']))
        data['flags'][raw.target]=result['success']
    elif raw.kind=='location':
        from .world import ensure
        place=next((p for p in state['locations'] if p['name']==raw.target),None)
        if not place:raise ValueError('Сценарная локация ещё не зарегистрирована')
        place['known_to_hero']=True;world=ensure(state)
        for node in state.get('geography',{}).get('nodes',[]):
            if node['name']==raw.target:node['known']=True
        world['observations'].setdefault(raw.target,dict(name=raw.target,description='Описание станет доступно после посещения.',objects=[],facts=[],npc=[]))
        if raw.public:notices.append(dict(kind='location',text=raw.text or 'Стало известно место: '+raw.target))
    elif raw.kind=='resource' and nat20:notices.append(dict(kind='resource',text=_nat20_resource(state,raw)))
    elif raw.kind=='resource':
        resource=state['hero'].get('resources',{}).get(raw.target)
        if resource is None or not isinstance(raw.value,int):raise ValueError('Неизвестный ресурс героя')
        resource['current']=max(0,min(resource['maximum'],resource['current']+raw.value))
    elif raw.kind=='companion':
        if raw.target not in data['companions'] or raw.value not in ('joined','left','missing','dead'):raise ValueError('Спутник и его последствие не подготовлены')
        data['companions'][raw.target]['status']=raw.value

def tick(state,results=None,*,rng=None,mechanics=True):
    data=state.get('adventure')
    if not data:return []
    definition=AdventureDefinition.model_validate(data['definition']);now=state.get('elapsed_minutes',0)
    if now<data.get('last_time',0):raise ValueError('Игровое время не может идти назад вне undo/restore')
    notices=[];budget=[200];tables={t.id:t for t in definition.tables};events={e.id:e for e in definition.events}
    from .adventure_expedition import tick_expedition
    notices.extend(tick_expedition(state,definition,rng))
    def effect(raw,record,depth=0):
        _execute(state,definition,raw,record,rng=rng,tables=tables,notices=notices,budget=budget,
                 depth=depth,mechanics=mechanics,nat20=False)
    changed=True;rounds=0
    while changed:
        changed=False;rounds+=1
        if rounds>30:raise ValueError('Слишком длинная цепочка событий')
        for event in definition.events:
            previous=data['events'].get(event.id,'pending')
            if previous=='resolved' and event.repeat_minutes is None:continue
            if event.instruction in ('recommendation','gm_note','alternative') or event.category=='optional':continue
            if any(data['events'].get(parent)!='resolved' for parent in event.prerequisites):continue
            if not all(satisfied(state,c) for c in event.conditions):continue
            if event.action_kind:
                matching=[r for r in results or [] if r.get('type')==event.action_kind or (r.get('type')=='scripted' and r.get('kind')==event.action_kind)]
                if event.action_target:matching=[r for r in matching if r.get('target')==event.action_target]
                if not matching:continue
            due=data['schedule'].get(event.id,event.after_minutes+(data.get('started_at',0) if event.time_anchor=='module' else 0))
            if due>now+1e-8:continue
            record=dict(id=event.id,source=event.source.model_dump(),category=event.category,status='resolved',game_time=now,due=due,rolls=[],effects=[e.model_dump() for e in event.effects])
            for raw in event.effects:effect(raw,record)
            data['events'][event.id]='resolved';data['journal'].append(record)
            if len(data['journal'])>5000:raise ValueError('Журнал сценария требует архивирования перед дальнейшим исполнением')
            if event.repeat_minutes is not None:data['schedule'][event.id]=due+event.repeat_minutes
            changed=True
    data['last_time']=now
    if definition.finish_conditions and all(satisfied(state,c) for c in definition.finish_conditions) and definition.id not in data['completed_modules']:
        data['completed_modules'].append(definition.id)
    return notices

def resolve_deferred(state,rng=None):
    """Execute what a Nat20 campaign left pending, with Nat20's own dice.

    The preparation routes (attach, continue, a world move) have no scene to
    roll in, so they only record a printed check, table, resource or reward as
    pending. This is their executor: it runs on a Nat20 screen turn, in the
    order the entries were written, inside the turn's own transaction. Each
    entry is settled by its declaration slot in the event's journal record, so
    an entry that was already executed is recognised as a repeat (a replayed
    queue books nothing) and two checks that differ only in their DC stay two
    distinct throws. An entry that cannot be finished changes nothing — the
    state is rolled back to before it — and stays in the queue with the reason
    printed, so nothing is dropped and nothing is applied twice.
    """
    data=state.get('adventure')
    if not data or 'nat20' not in state:return []
    queue=list(data.get('deferred') or [])
    if not queue:return []
    definition=AdventureDefinition.model_validate(data['definition'])
    tables={t.id:t for t in definition.tables}
    notices=[];remaining=[]
    for item in queue:
        records=[r for r in data.get('journal',[]) if r.get('id')==item.get('event')]
        record=next((r for r in reversed(records) if item in r.get('deferred',[])),records[-1] if records else None)
        marks=len(notices);budget=[200]
        saved=(copy.deepcopy(data),copy.deepcopy(state.get('nat20')))
        try:
            raw,index=_pending_effect(record,item)
            _execute(state,definition,raw,record,rng=rng,tables=tables,notices=notices,budget=budget,
                     depth=0,mechanics=True,nat20=True)
        except _Settled:
            del notices[marks:]
            continue
        except (NeedsScreen,ValueError,KeyError,StopIteration,IndexError) as exc:
            data.clear();data.update(saved[0]);del notices[marks:]
            if 'nat20' in state:state['nat20']=saved[1]
            remaining.append(item)
            notices.append(dict(kind='deferred',text='Сценарный эффект «'+str(item.get('kind'))+'»'
                +(' «'+str(item.get('text'))+'»' if item.get('text') else '')
                +' ожидает игрового экрана Nat20: '+str(exc)))
            continue
        record['settled']=sorted({*(record.get('settled') or []),index})
        record['deferred']=[p for p in record.get('deferred',[]) if p!=item]
    data['deferred']=remaining
    return notices

def after_transition(state,results,*,rng=None,before=None,mechanics=True,drain_deferred=False):
    if not state.get('adventure'):return
    if before and before.get('combat',{}).get('active') and state.get('combat'):
        difference=max(0,state['combat'].get('round',1)-before['combat'].get('round',1))
        state['elapsed_minutes']=state.get('elapsed_minutes',0)+difference*.1
    # A screen turn settles the backlog it inherited first, then lets the new
    # flags of this turn open the events that depend on them, then settles
    # whatever this turn itself printed as due. Both drains are idempotent.
    notices=resolve_deferred(state,rng=rng) if drain_deferred else []
    notices+=tick(state,results,rng=rng,mechanics=mechanics)
    if drain_deferred:notices+=resolve_deferred(state,rng=rng)
    if notices and results:results[-1].setdefault('adventure_events',[]).extend(notices)

class AdventureService:
    def __init__(self,store,library):self.store,self.library=store,library
    def startable(self,bundle,module=None):
        """An appendix is a reference: no adventure begins inside a bestiary.

        `startable` in the review card is what the screen draws, but the screen is
        not the rule — a request that names an appendix module is refused here,
        where the start actually happens. The type is read from the parsed scene,
        not from the instructions a person may rewrite.
        """
        if not module:return
        row=next((item for item in self.library.modules(bundle) if item['id']==module),None)
        if row and row.get('kind')=='appendix':
            raise ValueError('Приложение книги — справочник: приключение с него не начинается')
    def attach(self,campaign,bundle,request_id,confirm,module=None):
        if not confirm:raise ValueError('Подтвердите применение проверенного сценария')
        definition=self.library.definition(bundle,module);self.startable(bundle,module)
        def transition(state):
            if state.get('adventure'):raise ValueError('Используйте проверенный переход к следующему модулю; активное прохождение не перезаписывается')
            initialize(state,bundle,definition,self.library)
            return [dict(type='scripted',kind='adventure_start',text='Подключён проверенный сценарий: '+definition.title)]
        return self.store.record_scenario(campaign,request_id,'Подтверждаю подготовленный сценарий',transition)
    def continue_module(self,campaign,bundle,module,request_id,confirm,standalone=False):
        if not confirm:raise ValueError('Подтвердите переход к выбранному модулю')
        definition=self.library.definition(bundle,module);self.startable(bundle,module)
        def transition(state):
            previous=copy.deepcopy(state.get('adventure') or {})
            complete=previous.get('completed_modules',[])
            if definition.continuation_of and not all(id in complete for id in definition.continuation_of):
                if not standalone or not definition.alternate_start:raise ValueError('Не подтверждены последствия предыдущих модулей; самостоятельный старт не предусмотрен')
            elif standalone and not definition.alternate_start:raise ValueError('Самостоятельный старт не проверен в источнике')
            if not all(satisfied(state,c) for c in definition.start_conditions) and not (standalone and definition.alternate_start):raise ValueError('Не выполнены проверенные исходные условия')
            if definition.world:
                from .world import import_package
                # Existing locations/identities are never silently overwritten by another volume.
                import_package(state,definition.world)
            initialize(state,bundle,definition,self.library,standalone=standalone);current=state['adventure']
            archives=previous.pop('volumes',{});current['volumes']=archives
            if previous:current['volumes'][previous['module']]=previous
            current['completed_modules']=complete
            for key in ('flags','knowledge','known_media','media_records','media_bundles','companions','weather'):
                if key not in previous:continue
                if isinstance(previous[key],dict):current[key]={**previous[key],**current.get(key,{})}
                elif isinstance(previous[key],list):current[key]=list(dict.fromkeys([*previous[key],*current.get(key,[])]))
                elif current.get(key) is None:current[key]=previous[key]
            for id in current['trackers']:
                if id in previous.get('trackers',{}):current['trackers'][id]=previous['trackers'][id]
            if standalone:current['flags'].update(definition.alternate_flags)
            return [dict(type='scripted',kind='adventure_continuation',text='Переход к следующему модулю подтверждён. Герой, вещи, мир и память сохранены.')]
        return self.store.record_scenario(campaign,request_id,'Продолжаю приключение',transition)
