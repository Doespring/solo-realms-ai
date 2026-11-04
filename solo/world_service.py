"""World commands share the existing journal and travel resolver."""
import json,copy,uuid
from typing import Literal
from pydantic import Field
from .contracts import Strict
from .world import WorldSettings,ScenarioPackage,ensure,observe,import_package,project,settings,known,direction
from .rulesets import digest
from . import travel


def _recover_calendar(state,db,campaign,before):
    """Score the calendar events this command's clock move crossed.

    Runs inside the caller's BEGIN IMMEDIATE transaction: the clock, the charge
    pools and the recovery_events rows commit together or not at all. A campaign
    without a Nat20 runtime has no charge pools, so nothing happens.
    """
    if 'nat20' not in state:
        return []
    from . import nat20_recovery_service as service
    from .nat20_api import Nat20GameEngine
    after=int(state.get('elapsed_minutes',0) or 0)
    # Catch up from the scored frontier, not just this command's window: a
    # scenario wait or an adventure step may have crossed a boundary that no
    # other path noticed. before is kept as a lower bound for older saves.
    cursor=service.cursor_of(state)
    frontier=min(before,cursor)
    events=service.plan(frontier,after,state,skip=cursor)
    if not events:
        return []
    # The live nat20 mapping, so the advanced cursor is persisted with the rest.
    runtime=state['nat20']
    Nat20GameEngine._recover(state,db,campaign,campaign,events,runtime,started=int(before))
    return events

class WorldCommand(Strict):
    request_id: str=Field(min_length=1,max_length=80)
    expected_version: int | None=Field(default=None,ge=0)
    kind: Literal['settings','travel','explore','marker','delete_marker','decline_encounter']
    target: str=Field(default='',max_length=120)
    direction: Literal['север','юг','восток','запад'] | None=None
    coordinates: list[int]=Field(default_factory=list,max_length=2)
    text: str=Field(default='',max_length=200)
    marker: str=Field(default='',max_length=80)
    exit: str=Field(default='',pattern=r'^\d{0,3}$')
    settings: WorldSettings | None=None
    confirm: bool=False
class WorldImport(Strict):
    request_id: str=Field(min_length=1,max_length=80)
    expected_version: int | None=Field(default=None,ge=0)
    confirm: bool=False
    package: ScenarioPackage

def travel_guard(state):
    from .contracts import Hero
    from .rulesets import RulesetEngine,active
    from .inventory import InventoryEngine
    # When Nat20 runs the campaign it owns the hero's live mechanical numbers:
    # HP, conditions and exhaustion change inside the engine, and the legacy
    # state['hero'] is only a copy taken at the last projection. Reading the
    # copy let a poisoned, exhausted or dying hero walk at full speed. The
    # engine's own projection is the one authority; the copy stays for old saves
    # that were never touched by Nat20.
    if state.get('nat20'):
        from .nat20_api import Nat20GameEngine
        hero=Nat20GameEngine._hero(state['nat20'])
    else:
        hero=Hero.model_validate(state['hero'])
    # The mechanical backend is Nat20, and it keeps the live fight in
    # state['nat20'], not in the legacy state['combat'] map. Reading only the
    # legacy key let a hero walk away mid-fight at full speed. Ask the backend
    # that actually runs the combat; the legacy key stays readable for old saves.
    active_combat=False
    if state.get('combat',{}).get('active'):active_combat=True
    elif state.get('nat20'):
        # _bound reads the campaign envelope, not the bare state, and it is the
        # only reader that knows a Nat20 preset is pinned to dnd2024. When it
        # cannot answer, the guard must not silently assume "no fight": a
        # swallowed error here is what let the hero walk out of the encounter.
        from .nat20_api import Nat20GameEngine
        active_combat=bool(Nat20GameEngine._bound({'id':'travel-guard','state':state}).get('combat'))
    if active_combat:raise ValueError('Завершите бой прежде чем путешествовать')
    if state.get('hero_death') or hero.hp==0 or set(hero.conditions)&{'incapacitated','unconscious','paralyzed','stunned','petrified','grappled','restrained'}:raise ValueError('Герой сейчас не может идти')
    speed=RulesetEngine(active(state)).exhaustion_effects(hero)['speed']
    if state.get('economy'):
        inventory=InventoryEngine(state)
        if inventory.weight()>inventory.capacity():raise ValueError('Поклажа превышает переносимый вес')
        speed=max(0,speed-inventory.speed_penalty())
    if not speed:raise ValueError('Движение недоступно по текущим правилам')
    return speed

class WorldService:
    def __init__(self,store):self.store=store
    def preview(self,campaign,target):
        current=self.store.campaign(campaign);state=current['state'];speed=travel_guard(state)
        if not known(state,target):raise ValueError('Это место пока неизвестно герою')
        path,_=travel.itinerary(state,target)
        return dict(target=target,meters=sum(r['meters'] for r in path),minutes=sum(travel.duration(r,speed,state) for r in path),route=[r['description'] for r in path],version=current['version'],notice='Это план пешего пути. Обнаруженное препятствие или событие может остановить путешествие.')
    def execute(self,campaign,body,*,original=None,from_map=False):
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            def transition(state):
                from .first_playable import require_full_scene
                if body.kind not in ('settings','marker'):require_full_scene(state)
                if body.expected_version is not None and body.expected_version!=current['version']:raise ValueError('Мир изменился; обновите намерение')
                world=ensure(state)
                if body.kind=='settings':
                    if not body.confirm or body.settings is None:raise ValueError('Подтвердите изменение режима и настроек мира')
                    world['settings']=body.settings.model_dump();text='Настройки мира изменены. Прежние события и география сохранены.'
                elif body.kind=='marker':
                    if len(body.coordinates)!=2 or any(not 0<=n<80 for n in body.coordinates) or not body.text.strip():raise ValueError('Укажите координаты и название своей метки')
                    if len(world['markers'])>=200:raise ValueError('Достигнут лимит меток')
                    world['markers'].append(dict(id=str(uuid.uuid4()),coordinates=body.coordinates,text=body.text));text='Личная метка добавлена. Она не создаёт локацию или дорогу.'
                elif body.kind=='delete_marker':
                    if not any(m['id']==body.marker for m in world['markers']):raise ValueError('Метка не найдена')
                    world['markers']=[m for m in world['markers'] if m['id']!=body.marker];text='Личная метка удалена.'
                elif body.kind=='decline_encounter':
                    if not world.get('pending_encounter'):raise ValueError('Нет предложения столкновения')
                    world['pending_encounter']=None;text='Предложение столкновения отклонено. Боевые участники и победа не создавались.'
                else:
                    travel_guard(state)
                    # Walking spends real game minutes: calendar boundaries crossed
                    # on the way must recharge exactly as a rest does, in this same
                    # transaction, or a dawn walked through is simply lost.
                    clock_before=int(state.get('elapsed_minutes',0) or 0)
                    if from_map and not settings(state).map_travel:raise ValueError('Выбор путешествия на карте отключён. Включите его явно в настройках мира; текстовое путешествие доступно.')
                    if body.kind=='travel':
                        if not known(state,body.target):raise ValueError('Это место неизвестно герою')
                        result=travel.advance(state,body.target)
                    elif body.kind=='explore':
                        if settings(state).mode=='story':raise ValueError('Исследование неизвестной дороги доступно в песочнице или смешанном режиме')
                        graph=travel.geography(state);by_id={n['id']:n for n in graph['nodes']};options=[]
                        for r in graph['routes']:
                            if not r.get('known',True) or not r.get('explorable') or settings(state).mode not in r.get('modes',['story','sandbox','mixed']):continue
                            for start,end in [(r['from'],r['to']),*([(r['to'],r['from'])] if not r.get('one_way') else [])]:
                                if by_id[start]['name']==state['location'] and not known(state,by_id[end]['name']):
                                    options.append({**r,'from':start,'to':end})
                        if body.exit:
                            index=int(body.exit)
                            options=[options[index]] if index<len(options) else []
                        else:options=[r for r in options if direction(by_id[r['from']]['coordinates'],by_id[r['to']]['coordinates'])==body.direction]
                        if len(options)!=1:raise ValueError('В этом направлении нет одной однозначной доступной неисследованной дороги')
                        result=travel.advance_path(state,options,graph)
                    _recover_calendar(state,c,campaign,clock_before)
                    text=('Прибытие: '+state['location'] if result['arrived'] else result['text'])+f". Пройдено {result['meters']} м за {result['minutes']} минут."
                    return [dict(type='scripted',kind='world_travel',text=text,travel=result,memory_evidence=result.get('memory_evidence',[]))]
                observe(state);return [dict(type='scripted',kind='world',text=text)]
            return self.store.engine_event(c,current,body.request_id,original or 'Мир: '+body.kind,transition,digest(body.model_dump(exclude={'request_id'})))
    def import_world(self,campaign,body):
        if not body.confirm:raise ValueError('Подтвердите добавление структурированного сценария в канон')
        with self.store.connection(True) as c:
            current=self.store.campaign(campaign,c)
            def transition(state):
                if state.get('combat',{}).get('active'):raise ValueError('Завершите бой перед импортом географии')
                if body.expected_version is not None and body.expected_version!=current['version']:raise ValueError('Мир изменился')
                import_package(state,body.package)
                return [dict(type='scripted',kind='world_import',text='Подтверждено расширение мира: '+body.package.name+'. Существующие локации и история не перезаписаны.')]
            return self.store.engine_event(c,current,body.request_id,'Добавление структурированного сценария',transition,digest(body.model_dump(exclude={'request_id'})))