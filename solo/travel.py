"""Пространство стартового приключения. Рёбра задаёт сценарий, не LLM."""
import copy
import heapq
import itertools
import re
import math

NODES=[
    {'id':'tavern','name':'Таверна «Последний огонь»','coordinates':[0,0]},
    {'id':'tower','name':'Старая башня','coordinates':[2,0]},
    {'id':'river','name':'Берег реки','coordinates':[3,1]},
]
ROUTES=[
    {'from':'tavern','to':'tower','meters':2000,'minutes':30,'description':'лесная тропа среди сосен'},
    {'from':'tower','to':'river','meters':1000,'minutes':20,'description':'тропа от башни к реке'},
]

def norm(s):return ' '.join(s.lower().replace('ё','е').split())

def geography(state):
    if 'geography' in state:return copy.deepcopy(state['geography'])
    # Старые сохранения не переписываются при открытии. Метаданные добавляются
    # только в новый снимок реально исполненного путешествия/ожидания.
    names={p['name'] for p in state['locations']}
    return {'nodes':[copy.deepcopy(n) for n in NODES if n['name'] in names],
            'routes':[copy.deepcopy(r) for r in ROUTES if all(next(n['name'] for n in NODES if n['id']==r[k]) in names for k in ('from','to'))]}

def destination(text,state):
    from .world import known
    s=norm(text);names=[p['name'] for p in state['locations'] if known(state,p['name'])]
    exact=[p['name'] for p in state['locations'] if p['name'] in names and any(re.search(r'(?<!\w)'+re.escape(norm(n))+r'(?!\w)',s) for n in [p['name'],*p.get('aliases',[])])]
    if len(exact)==1:return exact[0]
    found=[]
    for name,pattern in [(NODES[0]['name'],r'таверн\w*'),(NODES[1]['name'],r'(?:стар\w*\s+)?башн\w*'),(NODES[2]['name'],r'рек\w*|берег\w*')]:
        if name in names and re.search(pattern,s):found.append(name)
    return found[0] if len(found)==1 else None

def itinerary(state,target):
    from .world import known,settings
    from .world_service import travel_guard
    speed=travel_guard(state)
    graph=geography(state);by_name={n['name']:n for n in graph['nodes']}
    if not known(state,target):raise ValueError('Это место ещё неизвестно герою')
    if state['location'] not in by_name or target not in by_name:
        raise ValueError('Я пока не знаю дороги в это место. Куда именно вы хотите отправиться?')
    source=by_name[state['location']]['id'];end=by_name[target]['id']
    if source==end:return [],graph
    edges={n['id']:[] for n in graph['nodes']}
    for r in graph['routes']:
        if not r.get('known',True) or settings(state).mode not in r.get('modes',['story','sandbox','mixed']):continue
        if not known(state,next(n['name'] for n in graph['nodes'] if n['id']==r['from'])) or not known(state,next(n['name'] for n in graph['nodes'] if n['id']==r['to'])):continue
        if r['minutes']<0 or r['meters']<0:raise ValueError('Маршрут повреждён; путешествие не началось.')
        edges.setdefault(r['from'],[]).append((r['to'],r))
        if not r.get('one_way'):
            edges.setdefault(r['to'],[]).append((r['from'],{**r,'from':r['to'],'to':r['from']}))
    counter=itertools.count();queue=[(0,source,next(counter),[])];visited=set()
    while queue:
        cost,node,_,path=heapq.heappop(queue)
        if node in visited:continue
        visited.add(node)
        if node==end:return path,graph
        for nxt,edge in edges.get(node,[]):
            if nxt not in visited:heapq.heappush(queue,(cost+duration(edge,speed,state),nxt,next(counter),path+[edge]))
    raise ValueError('Между этими местами ещё не известна дорога. Уточните маршрут.')

def duration(edge,speed,state=None):
    # Legacy adventure durations are authored estimates, not a universal speed formula.
    if edge.get('timing','scenario')=='scenario':return edge['minutes']
    from .rulesets import active
    preset=active(state or {});pace=edge.get('pace','normal')
    if preset.edition=='2024' and state and speed<=state['hero']['speed_ft']/2:pace='slow'
    rate=preset.travel_pace_ft[pace]/(2 if edge.get('difficult') else 1)
    return math.ceil(edge['meters']*10000/(rate*3048))

def advance(state,target):
    path,graph=itinerary(state,target)
    return advance_path(state,path,graph)

def advance_path(state,path,graph):
    from .world import ensure,observe,tick
    from .world_service import travel_guard
    speed=travel_guard(state);world=ensure(state);state['geography']=graph;state.setdefault('elapsed_minutes',0)
    by_id={n['id']:n for n in graph['nodes']};walked=[];minutes=0;meters=0;stop='';summaries=[];evidence=[]
    # Walking away declines only an uncommitted proposal; it never creates a battle victory.
    world['pending_encounter']=None
    messages,records=tick(state);summaries+=messages;evidence+=records
    if world.get('pending_encounter'):stop='Предложено новое столкновение. Сначала рассмотрите его или явно продолжите путь.'
    for edge in path:
        if stop:break
        if edge.get('blocked'):
            stop=edge.get('obstacle','Дорога перекрыта. Нужно решить, как пройти дальше.');break
        from .interactions import passage_guard
        try:passage_guard(state,by_id[edge['from']]['name'],by_id[edge['to']]['name'])
        except ValueError as exc:stop=str(exc);break
        elapsed=duration(edge,speed,state);world['traveling']=True
        state['elapsed_minutes']=state.get('elapsed_minutes',0)+elapsed
        messages,records=tick(state);summaries+=messages;evidence+=records
        world.pop('traveling',None)
        minutes+=elapsed;meters+=edge['meters'];walked.append(edge['description'])
        state['location']=by_id[edge['to']]['name'];state['location_id']=edge['to']
        observe(state);messages,records=tick(state);summaries+=messages;evidence+=records
        if world.get('pending_encounter'):
            stop='По прибытии подготовлено предложение столкновения; дальнейшие шаги остановлены.';break
        if edge.get('decision'):
            stop=edge['decision'];break
    observe(state)
    result={'type':'move','target':state['location'],'from':by_id[path[0]['from']]['name'] if path else state['location'],
            'minutes':minutes,'meters':meters,'route':walked,'arrived':not stop,'text':'Пешее путешествие'}
    if summaries:result['world_events']=summaries
    if evidence:result['memory_evidence']=evidence
    if stop:result.update(type='travel_stop',text=stop)
    return result
