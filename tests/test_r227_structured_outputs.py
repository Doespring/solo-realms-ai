"""r227: жёсткая схема ответа вместо «верни JSON».

Фикстуры собраны по реальному логу владельца (`runtime/web-local/preparation.sqlite3`):
`location_Dis_GodStreet`, `npc_Bezo_Cheka_Ump`, верхнеуровневые `locations`/
`routes`/`events`, связь `from`/`to` внутри сущности, `kind` вне списка,
элемент `entities` не словарь. Текст книги сюда не попадает — только форма.
"""
import asyncio,json
from decimal import Decimal

import httpx,pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud import CloudLLM
from solo.cloud_cost import CostLedger
from solo.cloud_settings import CloudProfile,CloudSettings,ModelConfig
from solo.preparation_contracts import PreparationBatch
from solo.preparation_format import decode,read,source_input,wire_schema
from solo.structured_output import dropped_paths,sanitize,strict_problems,strict_schema
from test_preparation_pipeline import PreparationFixture,new,original,terminal

#: Тот же грязный ответ, что пришёл от модели на книге владельца.
DIRTY=dict(title='Блок',ruleset='dnd2014',start='',
    locations=[dict(name='Улица Богов')],
    routes=[dict(from_='Место',to='Третье')],
    events=[dict(id='буря')],
    entities=[
        dict(key='loc',kind='Location',data=dict(name='Место')),
        dict(key='npc1',kind='npc',location='Место',data=dict(name='Безо')),
        dict(key='e3',kind='location',data=dict(name='Третье'),
             location_Dis_GodStreet=dict(name='Улица Богов'),npc_Bezo_Cheka_Ump=dict(name='Безо Чек')),
        dict(key='e4',kind='location',data=dict(name='Четвёртое'),
             locations=[dict(name='Вложенное')],routes=[],events=[],problems=['нет данных'],metadata_evidence=[]),
        'просто строка',
        [dict(key='вложенный')],
        dict(key='e7',kind='group',data=dict(name='Группа')),
        dict(key='e8',kind='RollTable',data=dict(name='Таблица наград')),
        dict(key='e9',kind='npc',location='Место',data=dict(name='Связной'),**{'from':'Место','to':'Третье'}),
    ],problems='нет данных',party_size='4')

EXPECTED_DROPPED=('locations','routes','events','entities.2.location_Dis_GodStreet',
    'entities.2.npc_Bezo_Cheka_Ump','entities.3.locations','entities.3.routes','entities.3.events',
    'entities.3.problems','entities.3.metadata_evidence','entities.4','entities.5',
    'entities.8.from','entities.8.to')


def answer(value=DIRTY):
    return json.dumps(value,ensure_ascii=False)


def test_strict_schema_is_built_from_pydantic_and_has_no_defaults():
    schema=strict_schema(PreparationBatch.model_json_schema())

    def walk(node,path=''):
        if isinstance(node,list):
            for i,item in enumerate(node):walk(item,path+'.'+str(i))
            return
        if not isinstance(node,dict):return
        assert 'default' not in node,path
        properties=node.get('properties')
        if isinstance(properties,dict) and properties:
            assert node.get('additionalProperties') is False,path
            assert list(node.get('required') or [])==list(properties),path
        for key,item in node.items():
            if key!='required':walk(item,path+'.'+key)
    walk(schema)
    # Свободный словарь (`data: dict`) и pattern/min/max strict-провайдер
    # выразить не может. Это не повод не отправлять схему, но и врать, что она
    # strict-валидна, нельзя: флаг снимается, причина названа.
    problems=strict_problems(schema)
    assert any(problem.endswith(':additionalProperties') for problem in problems),problems
    # Ссылка на фрагмент источника — часть wire-контракта, а не поля модели.
    assert 'anyOf' in wire_schema()['$defs']['SourceQuote']


@pytest.mark.parametrize('value',[DIRTY,
    {**DIRTY,'entities':[dict(key='loc',kind='location',data=dict(name='Место')),dict(key='npc1',kind='npc',data=dict(name='Безо'))]},
    {**DIRTY,'entities':dict(key='loc',kind='location',data=dict(name='Место'))},
    {**DIRTY,'entities':[dict(key='loc',kind='spaceship',data=dict(name='Корабль'))]}])
def test_owner_log_shapes_become_a_valid_model_and_are_reported(value):
    result,changes,report=read(answer(value),{})
    assert isinstance(result,PreparationBatch)
    assert 'Extra inputs are not permitted' not in json.dumps(changes)
    assert report['schema_dropped_keys'],'потеря обязана попасть в отчёт'


def test_extra_keys_are_named_with_their_path():
    result,changes,report=read(answer(DIRTY),{})
    dropped=report['schema_dropped_keys']
    for path in EXPECTED_DROPPED:assert path in dropped,path
    assert dropped_paths(sanitize(wire_schema(),DIRTY)[1])==dropped
    # Выброшенное не попадает в модель, а нормализованное — попадает.
    assert result.entities[0].kind=='location'
    assert [entity.kind for entity in result.entities]==['location','npc','location','location','npc','table','npc']


def test_single_object_and_scalar_are_turned_into_the_expected_list():
    result,changes,report=read(answer(DIRTY),{})
    assert result.problems==['нет данных'] and result.party_size==4
    assert 'problems:single_object_to_list' not in report['normalized']
    single,_,single_report=read(answer({**DIRTY,'entities':dict(key='loc',kind='location',data=dict(name='Место'))}),{})
    assert len(single.entities)==1 and 'single_object_to_list' in ' '.join(single_report['normalized'])


def test_list_that_is_not_a_dict_is_dropped_without_failing_the_block():
    result,changes,report=read(answer(DIRTY),{})
    assert 'entities.4' in report['schema_dropped_keys'] and 'entities.5' in report['schema_dropped_keys']


def test_sanitizer_never_returns_its_internal_sentinel():
    """Невалидный корень возвращается как есть: ошибку назовёт pydantic."""
    cleaned,notes=sanitize(wire_schema(),['не объект'])
    assert type(cleaned) is not object and cleaned==['не объект']
    assert notes[-1]['reason']=='root_invalid'
    # И исходный словарь тоже не превращается в сентинел.
    cleaned,notes=sanitize(wire_schema(),{'title':'x','ruleset':'5e','entities':[]})
    assert isinstance(cleaned,dict) and 'ruleset' not in cleaned
    assert {'path':'ruleset','action':'dropped','reason':'enum_out_of_list'} in notes


def test_strict_decode_still_refuses_an_extra_key():
    """Проверка wire-формата не ослаблена: санитайзер — только путь подготовки."""
    with pytest.raises(ValueError):decode(answer(DIRTY))


def test_provider_receives_response_format_json_schema_from_models(tmp_path):
    requests=[]
    def respond(request):requests.append(json.loads(request.content));return sse([{'choices':[{'delta':{'content':'{"title":"x"}'}}]}])
    llm,profile=configured(tmp_path,respond)
    assert collect(llm,grammar=wire_schema())=='{"title":"x"}'
    response_format=requests[0]['response_format']
    assert response_format['type']=='json_schema'
    # У PreparationBatch свободный `data`, поэтому схема уходит без strict-флага:
    # strict-провайдер отверг бы её 400-й, и схема не дошла бы вовсе.
    assert response_format['json_schema']['strict'] is False
    body=response_format['json_schema']['schema']
    entity=body['$defs']['PreparedEntity']
    assert entity['additionalProperties'] is False and 'default' not in json.dumps(entity)
    assert list(body['required'])==list(body['properties'])
    assert llm.metrics[-1]['schema_mode']=='json_schema' and llm.metrics[-1]['schema_bytes']>0
    assert llm.metrics[-1]['schema_strict'] is False
    assert llm.ledger.summary()['calls'][0]['schema_mode']=='json_schema'


def test_strict_flag_is_sent_when_the_schema_fits_strict_mode(tmp_path):
    """Схема без свободных словарей уходит с `strict: true`."""
    from solo.contracts import Strict
    class Contract(Strict):
        title:str
        count:int
    requests=[]
    def respond(request):requests.append(json.loads(request.content));return sse([{'choices':[{'delta':{'content':'{"title":"x","count":1}'}}]}])
    llm,profile=configured(tmp_path,respond)
    assert collect(llm,grammar=Contract.model_json_schema())=='{"title":"x","count":1}'
    assert requests[0]['response_format']['json_schema']['strict'] is True
    assert llm.metrics[-1]['schema_strict'] is True


def test_schema_refusal_falls_back_to_json_object_once_and_is_remembered(tmp_path):
    seen=[]
    def respond(request):
        body=json.loads(request.content);seen.append(body['response_format']['type'])
        if body['response_format']['type']=='json_schema':
            return httpx.Response(400,json={'error':{'message':'response_format json_schema is not supported by this route'}})
        return sse([{'choices':[{'delta':{'content':'{"title":"x"}'}}]}])
    llm,profile=configured(tmp_path,respond)

    async def run():
        try:
            pieces=[]
            # Один и тот же процесс: второй вызов не платит за ту же схему.
            for _ in range(2):
                async for piece in llm.stream('Роль','Реплика',grammar=wire_schema()):pieces.append(piece)
            return ''.join(pieces)
        finally:await llm.close()
    assert asyncio.run(run())=='{"title":"x"}{"title":"x"}'
    assert seen==['json_schema','json_object','json_object']
    assert llm.metrics[0]['error_code']=='schema_rejected' and llm.metrics[0]['provider_status']==400
    assert [m['schema_mode'] for m in llm.metrics]==['json_schema','json_object','json_object']
    assert llm.ledger.summary()['calls'][1].get('schema_mode') in ('json_object','')


def test_catalog_capability_decides_json_schema_versus_json_object():
    from solo.cloud_setup import configure_route
    base=ModelConfig(model='m',max_tokens=600,parameters=['max_tokens'],structured='json_schema')
    row=dict(tag='route',status=0,context_length=65536,max_completion_tokens=4096,
             supported_parameters=['max_tokens','response_format','structured_outputs'],pricing={'prompt':1,'completion':2},pricing_units={'prompt':'token','completion':'token'})
    assert configure_route(base,row)[0].structured=='json_schema'
    row['supported_parameters']=['max_tokens','response_format']
    assert configure_route(base,row)[0].structured=='json_object'


class RepairFixture(PreparationFixture):
    """Первый ответ грязный по-настоящему (сломанный элемент), второй — ремонт."""
    def __init__(self,repair_ok=True):
        super().__init__();self.repair_ok=repair_ok;self.calls=[];self.repairs=[]
    async def stream(self,system,user,**kwargs):
        payload=json.loads(user)
        self.metrics.append(dict(mode='preparation_world',mock=True))
        if payload.get('task')=='repair-preparation-block-v1':
            self.repairs.append(payload)
            if not self.repair_ok:yield '{"title":'
            else:yield json.dumps(original(),ensure_ascii=False)
            return
        self.calls.append(1)
        # Сущность без key санитайзер починить не может (придумывать имя нельзя),
        # поэтому это настоящий ремонт, а не чистка ключей.
        broken=original();broken['entities']=[dict(entity) for entity in broken['entities']]
        broken['entities'].append(dict(kind='location',data=dict(name='Без ключа'),**{'from':'Место','to':'Третье'}))
        yield json.dumps(broken,ensure_ascii=False)


def test_one_repair_call_gets_pydantic_errors_and_previous_answer(tmp_path):
    llm=RepairFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'История D&D 2014')
        run=terminal(client,client.post(url+'/prepare',json=dict(request_id='repair',ruleset='dnd2014',consent_to_send=True)).json()['id'])
        assert run['ready'],run
        assert len(llm.calls)==1 and len(llm.repairs)==1,'ровно один ремонт'
        sent=llm.repairs[0]
        assert sent['errors'] and 'previous_answer' in sent
        assert any('key' in line for line in sent['errors']),sent['errors']
        with client.app.state.preparation.connection() as db:
            metric=json.loads(db.execute('SELECT metric FROM creation_parts WHERE status="validated"').fetchone()[0])
        assert metric['repairs']==1 and metric['calls']==2
        assert metric['bytes']>0 and metric['duration_ms']>=0
        assert metric['schema_dropped_keys']


def test_second_failure_is_honest_and_does_not_repeat_the_repair(tmp_path):
    llm=RepairFixture(repair_ok=False)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'История D&D 2014')
        run=terminal(client,client.post(url+'/prepare',json=dict(request_id='repair',ruleset='dnd2014',consent_to_send=True)).json()['id'])
        assert not run['ready']
        assert len(llm.calls)==1 and len(llm.repairs)==1,'второй ремонт запрещён'
        assert run['errors'] and 'Extra inputs' not in json.dumps(run)
        with client.app.state.preparation.connection() as db:
            metric=json.loads(db.execute('SELECT metric FROM creation_parts').fetchone()[0])
        # Провал ремонта не должен выглядеть как «ремонта не было»: иначе
        # метрика снова врёт о том, сколько раз платили.
        assert metric['repairs']==1 and metric['calls']==2


def test_dirty_answer_without_repair_is_cleaned_and_counted(tmp_path):
    def dirty(payload):
        value=original()
        value['entities']=[dict(entity) for entity in value['entities']]
        value['locations']=[{'name':'Лишнее'}]                       # верхний уровень
        value['entities'][0]['routes']=[]                            # внутри сущности
        value['entities'][0]['problems']=['нет данных']
        value['entities'][2]['from']='Перекрёсток'                   # связь в NPC
        return value
    llm=PreparationFixture(dirty)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'История D&D 2014')
        run=terminal(client,client.post(url+'/prepare',json=dict(request_id='clean',ruleset='dnd2014',consent_to_send=True)).json()['id'])
        assert run['ready'],run
        with client.app.state.preparation.connection() as db:
            row=db.execute('SELECT metric,diagnostic FROM creation_parts').fetchone()
        metric=json.loads(row[0]);diagnostic=json.loads(row[1])
        assert metric['repairs']==0
        assert set(metric['schema_dropped_keys'])=={'locations','entities.0.routes','entities.0.problems','entities.2.from'}
        assert any(item['reason']=='schema_dropped_key' for item in diagnostic)
        assert not any('Extra inputs' in json.dumps(item) for item in diagnostic)


class TestVault:
    __test__=False
    def __init__(self):self.keys={}
    def exists(self,p):return p in self.keys
    def set(self,p,key):self.keys[p]=key
    def get(self,p):return self.keys.get(p)
    def delete(self,p):self.keys.pop(p,None)


def configured(tmp_path,respond):
    role=ModelConfig(model='test-model',max_tokens=1000,parameters=['max_tokens','temperature','response_format'],structured='json_schema',input_per_million=Decimal('2'),output_per_million=Decimal('4'))
    profile=CloudProfile(enabled=True,base_url='https://provider.example/v1',interpreter=role,narrator=role,streaming=True,
        max_request_cost=Decimal('10'),session_budget=Decimal('10'),daily_budget=Decimal('10'))
    settings=CloudSettings(tmp_path/'profiles.json',TestVault());settings.save(profile)
    settings.vault.set(profile.id,'synthetic-secret-only-for-tests')
    ledger=CostLedger(tmp_path/'usage.sqlite3')
    client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return CloudLLM(settings,ledger,client=client),profile


def sse(frames,done=True):
    text=''.join('data: '+json.dumps(frame,ensure_ascii=False)+'\n\n' for frame in frames)
    if done:text+='data: [DONE]\n\n'
    return httpx.Response(200,headers={'content-type':'text/event-stream'},content=text.encode())


def collect(llm,**kw):
    async def run():
        try:return ''.join([c async for c in llm.stream('Роль','Реплика',**kw)])
        finally:await llm.close()
    return asyncio.run(run())
