"""Reviewed scenario effects run inside the existing state/event transaction."""
import copy,re,secrets
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

def tick(state,results=None,*,rng=None):
    data=state.get('adventure')
    if not data:return []
    definition=AdventureDefinition.model_validate(data['definition']);now=state.get('elapsed_minutes',0)
    if now<data.get('last_time',0):raise ValueError('Игровое время не может идти назад вне undo/restore')
    notices=[];budget=[200];tables={t.id:t for t in definition.tables};events={e.id:e for e in definition.events}
    from .adventure_expedition import tick_expedition
    notices.extend(tick_expedition(state,definition,rng))
    def effect(raw,record,depth=0):
        budget[0]-=1
        if budget[0]<0 or depth>8:raise ValueError('Слишком много сценарных последствий; проверьте граф и шаг времени')
        if raw.kind=='notice':
            if raw.public and raw.text:notices.append(dict(text=raw.text,kind='notice'))
        elif raw.kind=='flag':data['flags'][raw.target]=raw.value
        elif raw.kind=='reward':
            # A printed reward is booked once, with its page. A reward the
            # rules cannot price yet is recorded as pending, never as zero.
            from .progression import RewardRule, book_progression
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
            table=tables[raw.target];rolled=roll(table.formula,rng);row=next(r for r in table.rows if r.low<=rolled['total']<=r.high)
            record['rolls'].append(dict(**rolled,table=table.id,row=dict(low=row.low,high=row.high,label=row.label),source=table.source.model_dump()))
            for nested in row.effects:effect(nested,record,depth+1)
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
        elif raw.kind=='resource':
            resource=state['hero'].get('resources',{}).get(raw.target)
            if resource is None or not isinstance(raw.value,int):raise ValueError('Неизвестный ресурс героя')
            resource['current']=max(0,min(resource['maximum'],resource['current']+raw.value))
        elif raw.kind=='companion':
            if raw.target not in data['companions'] or raw.value not in ('joined','left','missing','dead'):raise ValueError('Спутник и его последствие не подготовлены')
            data['companions'][raw.target]['status']=raw.value
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

def after_transition(state,results,*,rng=None,before=None):
    if not state.get('adventure'):return
    if before and before.get('combat',{}).get('active') and state.get('combat'):
        difference=max(0,state['combat'].get('round',1)-before['combat'].get('round',1))
        state['elapsed_minutes']=state.get('elapsed_minutes',0)+difference*.1
    notices=tick(state,results,rng=rng)
    if notices and results:results[-1].setdefault('adventure_events',[]).extend(notices)

class AdventureService:
    def __init__(self,store,library):self.store,self.library=store,library
    def attach(self,campaign,bundle,request_id,confirm,module=None):
        if not confirm:raise ValueError('Подтвердите применение проверенного сценария')
        definition=self.library.definition(bundle,module)
        def transition(state):
            if state.get('adventure'):raise ValueError('Используйте проверенный переход к следующему модулю; активное прохождение не перезаписывается')
            initialize(state,bundle,definition,self.library)
            return [dict(type='scripted',kind='adventure_start',text='Подключён проверенный сценарий: '+definition.title)]
        return self.store.record_scenario(campaign,request_id,'Подтверждаю подготовленный сценарий',transition)
    def continue_module(self,campaign,bundle,module,request_id,confirm,standalone=False):
        if not confirm:raise ValueError('Подтвердите переход к выбранному модулю')
        definition=self.library.definition(bundle,module)
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
