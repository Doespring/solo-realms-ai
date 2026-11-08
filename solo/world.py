"""Declarative world structure, observations and bounded scripted time events."""
import copy,uuid
from typing import Literal
from pydantic import Field,model_validator,ConfigDict
from .contracts import Strict
from .rulesets import digest
from .maps import MapDescription

Mode=Literal['story','sandbox','mixed']
def public_results(value):
    """Evidence is server-side memory input, including NPC-only observations."""
    if isinstance(value,dict):return {k:public_results(v) for k,v in value.items() if k not in ('memory_evidence','game_event','game_snapshot','engine_snapshot','native_events','native_result')}
    if isinstance(value,list):return [public_results(v) for v in value]
    return value
class WorldSettings(Strict):
    mode: Mode='story'
    map_travel: bool=False
    width: int=Field(default=12,ge=4,le=80)
    height: int=Field(default=8,ge=4,le=80)
class WorldNode(Strict):
    id: str=Field(min_length=1,max_length=80)
    name: str=Field(min_length=1,max_length=120)
    coordinates: list[int]=Field(min_length=2,max_length=2)
    known: bool=True
    environment: Literal['room','forest_road','ruins','bridge','balcony','cellar']='room'
    @model_validator(mode='after')
    def valid(self):
        if any(not 0<=n<80 for n in self.coordinates):raise ValueError('Координаты мира вне диапазона')
        return self
class WorldRoute(Strict):
    # Existing stored definitions used the Python field name. Accept them without
    # rewriting user records; new persistent definitions use the public alias.
    model_config=ConfigDict(extra='forbid',populate_by_name=True)
    from_: str=Field(alias='from',min_length=1,max_length=80)
    to: str=Field(min_length=1,max_length=80)
    meters: int=Field(ge=1,le=100000000)
    minutes: int=Field(ge=1,le=100000)
    timing: Literal['scenario','srd_pace']='scenario'
    pace: Literal['slow','normal','fast']='normal'
    difficult: bool=False
    description: str=Field(min_length=1,max_length=300)
    known: bool=True
    explorable: bool=False
    one_way: bool=False
    blocked: bool=False
    obstacle: str=Field(default='Дорога перекрыта.',max_length=300)
    modes: list[Mode]=Field(default_factory=lambda:['story','sandbox','mixed'],min_length=1,max_length=3)
class WorldNPC(Strict):
    name: str=Field(min_length=1,max_length=80)
    character: str=Field(default='',max_length=1000)
    biography: str=Field(default='',max_length=2000)
    motivation: str=Field(default='',max_length=1000)
    mood: str=Field(default='',max_length=300)
    goals: list[str]=Field(default_factory=list,max_length=10)
    known: str=Field(default='',max_length=1200)
    private_knowledge: list[str]=Field(default_factory=list,max_length=20)
    present: bool=True
    @model_validator(mode='after')
    def valid(self):
        if any(len(s)>1000 for s in self.goals+self.private_knowledge):raise ValueError('Запись NPC слишком длинная')
        return self
class WorldLocation(Strict):
    name: str=Field(min_length=1,max_length=120)
    description: str=Field(default='',max_length=3000)
    npc: list[WorldNPC]=Field(default_factory=list,max_length=30)
    objects: list[str]=Field(default_factory=list,max_length=80)
    # Additive declarations; legacy prose objects do not acquire invented mechanics.
    interactions: list['SceneObject']=Field(default_factory=list,max_length=80)
    aliases: list[str]=Field(default_factory=list,max_length=12)
    facts: list[str]=Field(default_factory=list,max_length=80)
    secrets: list[str]=Field(default_factory=list,max_length=80)
    # The book's own prose for this place, kept apart from what the party may
    # read. A source place with no read-aloud box publishes no description, but
    # the GM still narrates from the printed words instead of a stub.
    sealed_text: str=Field(default='',max_length=3000)
    known_to_hero: bool=False
    @model_validator(mode='after')
    def valid(self):
        if len({o.id for o in self.interactions})!=len(self.interactions):raise ValueError('Повтор объекта взаимодействия')
        if any(not a.strip() or len(a)>120 for a in self.aliases):raise ValueError('Некорректное имя помещения')
        if any(len(x)>1000 for x in self.objects+self.facts+self.secrets):raise ValueError('Сведение локации слишком длинное')
        return self
from .interactions import SceneObject,objects as scene_objects
WorldLocation.model_rebuild()
class WorldEvent(Strict):
    id: str=Field(min_length=1,max_length=80)
    after_minutes: int=Field(default=0,ge=0,le=10000000)
    location: str=Field(default='',max_length=120)
    kind: Literal['notice','reveal','npc_move','relationship','encounter']
    text: str=Field(default='',max_length=800)
    npc: str=Field(default='',max_length=80)
    target: str=Field(default='',max_length=120)
    relationship: int=Field(default=0,ge=-100,le=100)
    encounter: MapDescription | None=None
    lethal_risk: bool=False
    @model_validator(mode='after')
    def valid(self):
        if self.kind=='notice' and not self.text.strip():raise ValueError('Публичному событию нужен текст')
        if self.kind in ('reveal','npc_move') and not self.target:raise ValueError('Событию нужна целевая локация')
        if self.kind in ('npc_move','relationship') and not self.npc:raise ValueError('Событию нужен NPC')
        return self
class ScenarioPackage(Strict):
    version: Literal[1]=1
    name: str=Field(min_length=1,max_length=120)
    source: str=Field(min_length=1,max_length=500)
    locations: list[WorldLocation]=Field(default_factory=list,max_length=100)
    nodes: list[WorldNode]=Field(default_factory=list,max_length=100)
    routes: list[WorldRoute]=Field(default_factory=list,max_length=200)
    events: list[WorldEvent]=Field(default_factory=list,max_length=100)
    @model_validator(mode='after')
    def valid(self):
        if len({p.name for p in self.locations})!=len(self.locations) or len({n.id for n in self.nodes})!=len(self.nodes) or len({e.id for e in self.events})!=len(self.events):raise ValueError('Повтор объекта сценария')
        return self

def public_npc(npc,*,audience='hero'):
    if npc.get('scope',npc.get('visibility')) in ('secret','gm') or ('known_by' in npc and audience not in npc['known_by']):return None
    def visible(value):
        if isinstance(value,dict):
            if value.get('scope',value.get('visibility')) in ('secret','gm'):return None
            return {k:v for k,item in value.items() if k not in ('secrets','private','gm_only') and (v:=visible(item)) is not None}
        if isinstance(value,list):return [v for item in value if (v:=visible(item)) is not None]
        return copy.deepcopy(value)
    result={k:v for k,item in npc.items() if k in ('id','name','character','mood','known','present','relationships') and (v:=visible(item)) is not None}
    # Explicitly published metadata from older saves remains usable; unscoped motives stay private.
    for field in ('goals','motivation','biography'):
        raw=npc.get(field)
        if isinstance(raw,list):
            public=[visible(v) for v in raw if isinstance(v,dict) and v.get('scope')=='public']
            if public:result[field]=public
        elif isinstance(raw,dict) and raw.get('scope')=='public':result[field]=visible(raw)
    return result
def location_view(place,*,audience='hero'):
    from .interactions import public_objects
    registered=public_objects(place)
    return dict(name=place['name'],description=place['description'],objects=list(dict.fromkeys(place.get('objects',[])+[o['name'] for o in registered])),interactions=registered,facts=copy.deepcopy(place.get('facts',[])),npc=[visible for n in place.get('npc',[]) if n.get('present',True) and (visible:=public_npc(n,audience=audience)) is not None])
def settings(state):return WorldSettings.model_validate(state.get('world',{}).get('settings',{}))
def ensure(state):
    from .travel import geography
    if 'world' not in state:
        state['world']=dict(settings=WorldSettings().model_dump(),visited=[],observations={},triggered=[],events=[],markers=[],chronicle=[],sources=[],pending_encounter=None)
        # Keep knowledge already exposed by legacy saves; future changes use observations.
        for p in state['locations']:
            if p.get('known_to_hero',True):state['world']['observations'][p['name']]=location_view(p)
    world=state['world']
    for k,value in dict(visited=[],observations={},triggered=[],events=[],markers=[],chronicle=[],sources=[],pending_encounter=None).items():world.setdefault(k,value)
    if not world.get('initialized'):
        for p in state['locations']:
            if known(state,p['name']):world['observations'].setdefault(p['name'],location_view(p))
        world['initialized']=True
    return world
def observe(state):
    world=ensure(state)
    # A world whose material never printed its opening place has no agreed
    # location yet. The Master asks for it in dialogue, so observing nothing
    # is the honest result here; a missing place must not crash the turn.
    place=next((p for p in state['locations'] if p['name']==state['location']),None)
    if place is None:return
    place['known_to_hero']=True
    world['observations'][place['name']]=location_view(place)
    if place['name'] not in world['visited']:world['visited'].append(place['name'])
    if 'geography' in state:
        for node in state['geography']['nodes']:
            if node['name']==place['name']:node['known']=True
def known(state,name):
    place=next((p for p in state['locations'] if p['name']==name),None)
    if place and name==state['location']:return True
    if not place or not place.get('known_to_hero',True):return False
    graph=state.get('geography',{}) or {};node=next((n for n in graph.get('nodes',[]) if n['name']==name),None)
    return node is None or node.get('known',True)
def locations_view(state):
    world=state.get('world',{});observations=world.get('observations',{});result=[]
    for p in state['locations']:
        if not known(state,p['name']):continue
        if p['name']==state['location']:view=location_view(p)
        else:view=copy.deepcopy(observations.get(p['name'],location_view(p)))
        result.append(view)
    return result
def direction(a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    if abs(dx)>=abs(dy):return 'восток' if dx>0 else 'запад'
    return 'юг' if dy>0 else 'север'
def project(state):
    from .travel import geography
    config=settings(state);graph=geography(state);by_id={n['id']:n for n in graph['nodes']};nodes=[{**{k:v for k,v in n.items() if k in ('id','name','coordinates','environment')},'visited':n['name'] in state.get('world',{}).get('visited',[]) or n['name']==state['location']} for n in graph['nodes'] if known(state,n['name'])]
    ids={n['id'] for n in nodes};routes=[];exits=[]
    for r in graph['routes']:
        if not r.get('known',True) or config.mode not in r.get('modes',['story','sandbox','mixed']):continue
        if r['from'] in ids and r['to'] in ids:
            routes.append({k:v for k,v in r.items() if k in ('from','to','meters','minutes','description','one_way')})
        if config.mode in ('sandbox','mixed') and r.get('explorable'):
            for start,end in [(r['from'],r['to']),*([(r['to'],r['from'])] if not r.get('one_way') else [])]:
                if start in ids and end not in ids and by_id[start]['name']==state['location']:
                    exits.append(dict(ref=str(len(exits)),direction=direction(by_id[start]['coordinates'],by_id[end]['coordinates']),label='Неисследованная дорога'))
    world=state.get('world',{});pending=copy.deepcopy(world.get('pending_encounter'))
    if pending:pending['description'].pop('seed',None)
    return dict(settings=config.model_dump(),nodes=nodes,routes=routes,exits=exits,markers=world.get('markers',[]),chronicle=world.get('chronicle',[])[-30:],elapsed_minutes=state.get('elapsed_minutes',0),current=state['location'],pending_encounter=pending)
def tick(state):
    world=ensure(state);now=state.get('elapsed_minutes',0);summaries=[];evidence=[]
    for raw in sorted(world.get('events',[]),key=lambda e:(e['after_minutes'],e['id'])):
        event=WorldEvent.model_validate(raw)
        if event.id in world['triggered'] or event.after_minutes>now or event.location and (event.location!=state['location'] or world.get('traveling')):continue
        visible=False;text=''
        if event.kind=='notice':
            visible=True;text=event.text
        elif event.kind=='reveal':
            place=next(p for p in state['locations'] if p['name']==event.target);place['known_to_hero']=True
            for node in state['geography']['nodes']:
                if node['name']==event.target:node['known']=True
            if place['name']==state['location']:world['observations'][place['name']]=location_view(place)
            else:world['observations'][place['name']]=dict(name=place['name'],description='Описание станет доступно после посещения.',objects=[],facts=[],npc=[])
            visible=True;text=event.text or 'Стало известно место: '+event.target
        elif event.kind=='npc_move':
            origin=next((p for p in state['locations'] if any(n['name']==event.npc and n.get('present',True) for n in p['npc'])),None)
            if origin is None:continue
            npc=next(n for n in origin['npc'] if n['name']==event.npc and n.get('present',True));target=next(p for p in state['locations'] if p['name']==event.target)
            if origin['name']==target['name']:world['triggered'].append(event.id);continue
            npc['present']=False;arrival=copy.deepcopy(npc);arrival['present']=True
            old=next((n for n in target['npc'] if n['name']==event.npc),None)
            if old:old.update(arrival)
            else:target['npc'].append(arrival)
            visible=not world.get('traveling') and state['location'] in (origin['name'],target['name']);text=event.npc+(' покидает сцену.' if state['location']==origin['name'] else ' теперь присутствует в сцене.')
            if state.get('economy'):
                for m in state['economy']['merchants']:
                    if m['name']==event.npc:m['location']=target['name']
            evidence.append(dict(text=event.npc+' переместился: '+event.target,kind='travel',provenance='scenario',truth='fact',scope='private',known_by=[npc.get('id',event.npc)],subjects=[npc.get('id',event.npc)],importance=6,source_ref='state.world.events:'+event.id))
        elif event.kind=='relationship':
            for p in state['locations']:
                for npc in p['npc']:
                    if npc['name']==event.npc:
                        npc.setdefault('relationships',{})['hero']=dict(trust=event.relationship,reason=event.text)
                        visible=visible or not world.get('traveling') and p['name']==state['location'] and npc.get('present',True)
            if state.get('economy'):
                for m in state['economy']['merchants']:
                    if m['name']==event.npc:m['relationship']=event.relationship
            text=event.text or 'Отношение '+event.npc+' изменилось по подтверждённому событию.'
        elif event.kind=='encounter':
            if world.get('traveling'):continue
            if world.get('pending_encounter') or state.get('combat',{}).get('active'):continue
            node=next((n for n in state.get('geography',{}).get('nodes',[]) if n['name']==state['location']),{})
            descriptor=event.encounter or MapDescription(environment=node.get('environment','room'))
            world['pending_encounter']=dict(id=event.id,location=state['location'],description=descriptor.model_dump(),lethal_risk_announced=event.lethal_risk,text=event.text or 'Подготовлено предложение столкновения.')
            visible=True;text=world['pending_encounter']['text']+(' Серьёзный смертельный риск объявлен до начала боя.' if event.lethal_risk else '')
        world['triggered'].append(event.id)
        if visible:
            summaries.append(text);world['chronicle'].append(dict(minutes=now,text=text,source=event.id))
            evidence.append(dict(text=text,kind='event',provenance='scenario',truth='fact',scope='private',known_by=['hero'],importance=7,source_ref='state.world.events:'+event.id))
    world['chronicle']=world['chronicle'][-200:]
    if not world.get('traveling'):observe(state)
    return summaries,evidence

def import_package(state,package):
    from .travel import geography
    world=ensure(state);graph=geography(state)
    if len(state['locations'])+len(package.locations)>200 or len(world['events'])+len(package.events)>300:raise ValueError('Превышен лимит локального мира')
    old_names={p['name'] for p in state['locations']}
    if any(p.name in old_names for p in package.locations):raise ValueError('Импорт не перезаписывает существующие локации')
    names=old_names|{p.name for p in package.locations};by_id={n['id']:n for n in graph['nodes']}
    ids=[o.id for p in state['locations'] for o in scene_objects(p)]+[o.id for p in package.locations for o in p.interactions]
    if len(ids)!=len(set(ids)):raise ValueError('Идентификатор взаимодействия уже занят')
    if any(o.destination and o.destination not in names for p in package.locations for o in p.interactions):raise ValueError('Дверь ведёт в неизвестное помещение')
    for node in package.nodes:
        if node.name not in names:raise ValueError('Узел не имеет локации')
        if node.id in by_id:
            if by_id[node.id]['name']!=node.name:raise ValueError('Идентификатор узла занят другой локацией')
        else:
            if any(n['name']==node.name for n in graph['nodes']):raise ValueError('Локация уже имеет узел карты')
            graph['nodes'].append(node.model_dump());by_id[node.id]=node.model_dump()
    if any(p.name not in {n['name'] for n in graph['nodes']} for p in package.locations):raise ValueError('Каждой новой локации нужен узел карты')
    if len(graph['nodes'])>200 or len(graph['routes'])+len(package.routes)>400:raise ValueError('Превышен лимит географии')
    for route in package.routes:
        if route.from_ not in by_id or route.to not in by_id or route.from_==route.to:raise ValueError('Маршрут ссылается на неизвестные/совпадающие узлы')
        if any({r['from'],r['to']}=={route.from_,route.to} for r in graph['routes']):raise ValueError('Маршрут между узлами уже существует')
        graph['routes'].append(route.model_dump(by_alias=True))
    npc_names={n['name'] for p in state['locations'] for n in p['npc']}
    new_npcs=[n.name for p in package.locations for n in p.npc]
    if len(set(new_npcs))!=len(new_npcs) or npc_names & set(new_npcs):raise ValueError('Имена новых NPC должны однозначно определять личность')
    for event in package.events:
        if event.id in {e['id'] for e in world['events']}:raise ValueError('Идентификатор события уже используется')
        if event.location and event.location not in names or event.kind in ('reveal','npc_move') and event.target not in names:raise ValueError('Неизвестная локация события')
        if event.kind in ('npc_move','relationship') and event.npc not in npc_names|set(new_npcs):raise ValueError('Неизвестный NPC события')
    for place in package.locations:
        value=place.model_dump()
        for npc in value['npc']:npc['id']='npc-'+digest([package.name,npc['name']])[:24]
        state['locations'].append(value)
        if value['known_to_hero']:world['observations'][value['name']]=dict(name=value['name'],description='Описание станет доступно после посещения.',objects=[],facts=[],npc=[])
    state['geography']=graph;world['events']+= [e.model_dump() for e in package.events]
    world['sources'].append(dict(name=package.name,source=package.source,version=package.version))
    observe(state)
