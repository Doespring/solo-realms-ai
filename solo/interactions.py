"""Registered, map-free scene interactions. Prose never creates an object or effect."""
import copy,re
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Strict,Hero,Step,Plan
from .inventory import Item,InventoryEngine
from .travel import norm

class SceneObject(Strict):
    id: str=Field(min_length=1,max_length=80)
    name: str=Field(min_length=1,max_length=120)
    aliases: list[str]=Field(default_factory=list,max_length=12)
    kind: Literal['door','item','scenery']='scenery'
    description: str=Field(default='',max_length=2000)
    visible: bool=True
    accessible: bool=True
    lock: Literal['unknown','unlocked','locked']='unknown'
    opened: bool=False
    destination: str=Field(default='',max_length=120)
    item: Item | None=None
    taken: bool=False
    @model_validator(mode='after')
    def coherent(self):
        if any(not a.strip() or len(a)>120 for a in self.aliases):raise ValueError('Некорректное имя объекта')
        if self.kind=='item' and (self.item is None or self.item.owner!='scene' or not self.item.quantity):raise ValueError('Подбираемому предмету нужны количество, источник и владелец scene')
        if self.kind!='item' and self.item is not None:raise ValueError('Предмет привязан к неподходящему объекту')
        if self.kind!='door' and (self.opened or self.destination or self.lock!='unknown'):raise ValueError('Состояние двери у другого объекта')
        if self.opened and self.lock=='locked':raise ValueError('Запертая дверь не может быть открыта')
        return self

def objects(place):return [SceneObject.model_validate(v) for v in place.get('interactions',[])]
def public_objects(place):
    return [dict(id=o.id,name=o.name,description=o.description,kind=o.kind,**({'opened':o.opened} if o.kind=='door' else {})) for o in objects(place) if o.visible and not o.taken]

VERBS=r'(открываю|пытаюсь открыть|беру|поднимаю|осматриваю|изучаю|использую|пью|выпиваю)'
def verb(text):
    return re.match(r'^(?:я\s+)?'+VERBS+r'\s+(.+?)[.! ]*$',norm(text))
def quick(text,state):
    # Only one exact clause; compound/conditional/free prose still uses Interpreter.
    s=norm(text)
    if re.search(r'[,;«»"]|\b(?:и|затем|потом|если|не|сначала|позже)\b',s):return None
    m=verb(s)
    if not m:return None
    if len(s)>240:return None
    kind='observe' if m[1] in ('осматриваю','изучаю') else 'use' if m[1] in ('использую','пью','выпиваю') else 'other'
    return Plan(summary='Взаимодействие с объектом',steps=[Step(kind=kind,text=text,target=m[2].strip('.! '))])

def names_object(query,obj):
    value=norm(query);names={norm(obj.id),norm(obj.name),*[norm(a) for a in obj.aliases]}
    if value in names:return True
    # Conservative nominative/accusative agreement for named doors. Keep every
    # adjective and noun: "деревянную" cannot be rebound to "железная".
    if obj.kind!='door' or not value.endswith(' дверь'):return False
    words=value.split()
    for name in names:
        canonical=name.split()
        if len(words)!=len(canonical) or not name.endswith(' дверь'):continue
        if all(a==b or b.endswith('ая') and a==b[:-2]+'ую' or b.endswith('яя') and a==b[:-2]+'юю' for a,b in zip(words,canonical)):return True
    return False

def locate(state,query):
    place=next(p for p in state['locations'] if p['name']==state['location'])
    found=[o for o in objects(place) if o.visible and names_object(query,o)]
    return place,found

def result(status,text,**more):
    return dict(type='interaction' if status=='supported' else 'needs_context' if status=='clarification' else 'unsupported' if status.endswith('unsupported') else 'impossible',status=status,text=text,reply=text,**more)

def classify(step,state):
    m=verb(step.text)
    if not m:return None
    query=step.item or step.target or m[2].strip('.! ')
    place,found=locate(state,query)
    # A model target may disambiguate a named object, never substitute another one.
    if found and not any(names_object(m[2].strip('.! '),o) for o in found):
        return result('clarification','Название объекта в действии и выбранная цель не совпадают. Уточните намерение.')
    action={'открываю':'open','пытаюсь открыть':'open','беру':'pickup','поднимаю':'pickup','осматриваю':'examine','изучаю':'examine','использую':'use','пью':'use','выпиваю':'use'}[m[1]]
    if action=='examine' and not found and not step.target:return None
    if action=='use':
        inventory=InventoryEngine(state)
        from .inventory_api import ALIASES,normal
        candidates=[i for i in inventory.data.items if i.owner=='hero' and i.quantity and normal(query) in {normal(i.name),normal(i.id),*[normal(a) for a in ALIASES.get(i.catalog,[])]}]
        if len(candidates)==1:
            item=candidates[0]
            if normal(m[2].strip('.! ')) not in {normal(item.name),normal(item.id),*[normal(a) for a in ALIASES.get(item.catalog,[])]}:return result('clarification','Название предмета в действии и выбранная цель не совпадают.')
            return ('use',item.id)
        if not found:return result('clarification','Укажите один доступный предмет из инвентаря.')
    if not found:return result('clarification','Состояние этого объекта не зарегистрировано. Уточните объект; результат пока не рассчитан.')
    if len(found)!=1:return result('clarification','Здесь несколько подходящих объектов. Укажите точное название.')
    obj=found[0]
    if obj.taken or not obj.accessible:return result('impossible','Объект сейчас недоступен.',object=obj.name)
    if action=='use' and obj.kind=='item' and obj.item.catalog in ('healing_potion','rations'):return ('use',obj.item.id)
    if action=='examine':return (action,obj.id)
    if action=='open':
        if obj.kind!='door':return result('clarification','Для объекта не зарегистрировано открывание.')
        if obj.lock=='unknown':return result('clarification','Состояние замка неизвестно; автоматического успеха нет.')
        if obj.lock=='locked':return result('impossible','Дверь заперта. Ключ или взлом требуют отдельной зарегистрированной механики.',object=obj.name)
        return (action,obj.id)
    if action=='pickup' and obj.kind=='item':return (action,obj.id)
    return result('critical_unsupported','Для этого объекта такое действие не реализовано. Эффект не придуман.')

def perform(state,step,rng=None):
    bound=classify(step,state)
    if bound is None:return None
    if isinstance(bound,dict):return bound
    action,id=bound
    if state.get('combat',{}).get('active'):return result('impossible','В бою используйте зарегистрированное боевое взаимодействие.')
    hero=Hero.model_validate(state['hero'])
    if state.get('hero_death') or hero.hp==0 or set(hero.conditions)&{'incapacitated','unconscious','paralyzed','stunned','petrified'}:return result('impossible','Герой не может выполнить действие в текущем состоянии.')
    # A failing suboperation cannot leak a partial transfer/heal into the event snapshot.
    trial=copy.deepcopy(state)
    try:
        if action=='use':
            inv=InventoryEngine(trial,rng=rng);effect=inv.use(id);inv.flush()
            reply=effect['text'];extra={'effect':effect}
        else:
            place=next(p for p in trial['locations'] if p['name']==trial['location'])
            raw=next(o for o in place['interactions'] if o['id']==id);obj=SceneObject.model_validate(raw);extra={'object':obj.name}
            if action=='examine':reply=obj.description or 'Подробное описание объекта пока не зарегистрировано.'
            elif action=='open':
                reply='Дверь уже открыта.' if obj.opened else 'Дверь открыта.';raw['opened']=True
            else:
                inv=InventoryEngine(trial);item=obj.item.model_copy(deep=True)
                if any(i.id==item.id for i in inv.data.items):raise ValueError('Этот предмет уже учтён в инвентаре')
                item.owner='hero';inv.data.items.append(item);inv.assert_capacity();inv.flush();raw['taken']=True
                reply='Поднят предмет: '+item.name+'.';extra['item_id']=item.id
        state.clear();state.update(trial)
        return result('supported',reply,action=action,**extra)
    except ValueError as exc:return result('impossible',str(exc))

def passage_guard(state,source,destination):
    # Either side of a declared door guards the passage, including direct world commands.
    for place in state['locations']:
        for obj in objects(place):
            if obj.kind=='door' and obj.destination and {place['name'],obj.destination}=={source,destination}:
                if not obj.accessible or obj.lock!='unlocked' or not obj.opened:raise ValueError('Проход закрыт дверью. Сначала откройте её; неизвестное состояние не считается успехом.')
