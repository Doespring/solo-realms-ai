"""r228: книга режется по главам, а локальный потолок ответа дробит секцию.

Потолок в 96 КБ — свойство роли (max_tokens × 16 + slack), а не провайдера.
Поэтому проверяем не «стало тихо», а три вещи: секции кончаются на границе
главы, пойманный `stream_byte_limit` делит секцию пополам и доводит дело до
результата, а когда дробить уже нечего — ошибка называет фрагмент книги.
"""
import json,re,time
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.cloud import CloudUnavailable,STREAM_BYTES_PER_TOKEN,STREAM_BYTE_SLACK
from solo.preparation_format import HEADING,section_pieces,split_pages,starts_heading
from test_preparation_pipeline import PreparationFixture,new,terminal


def book(chapters=4,paragraphs=40):
    text='';number=0
    for chapter in range(1,chapters+1):
        text+='Глава '+str(chapter)+'. Испытание\n'
        for _ in range(paragraphs):
            number+=1
            text+='Дом'+str(number)+'. Зал с колоннами и факелами. Вход охраняет стража.\n'
    return text


def test_sections_start_at_chapters_and_never_lose_a_character():
    text=book(chapters=5,paragraphs=60)
    pieces=section_pieces(text,4000)
    assert ''.join(piece for _,piece in pieces)==text
    assert all(0<len(piece)<=4000 for _,piece in pieces)
    bounds=[0]+[match.start() for match in HEADING.finditer(text) if match.start()>0]+[len(text)]
    for offset,piece in pieces:
        # Кусок не пересекает границу главы: сущность со стыка не теряется.
        assert any(start<=offset and offset+len(piece)<=end for start,end in zip(bounds,bounds[1:])),offset
    assert [piece for _,piece in pieces if starts_heading(piece)]
    # Заголовок внутри прозы не режет сцену: это не глава, а фраза.
    assert not starts_heading('Глава дружины шла вперёд и пела.')
    assert starts_heading('Глава 2. Башня\nБашня стоит у реки.')


def test_split_below_minimum_returns_no_children():
    small=[dict(document='a'*64,page=1,offset=0,text='абв\n'*400)]
    assert sum(len(p['text']) for p in small)<2000 and split_pages(small)==[]
    assert split_pages([dict(document='a'*64,page=2,offset=0,text='абв\n'*2000)],minimum=60000)==[]


def test_local_limit_refusal_carries_machine_readable_code(tmp_path,monkeypatch):
    import solo.cloud as cloud
    from test_cloud import configured,collect,sse
    monkeypatch.setattr(cloud,'stream_budget',lambda p,t:dict(bytes=200,lines=10**6,events=10**6,seconds=60))
    frames=[{'choices':[{'delta':{'content':'я'*80}}]} for _ in range(5)]
    llm,_=configured(tmp_path,lambda request:sse(frames))
    with pytest.raises(CloudUnavailable) as exc:collect(llm)
    assert exc.value.error_code=='stream_byte_limit' and exc.value.limit_bytes==200


class SectionFixture(PreparationFixture):
    """Отвечает по секции: имя берёт из текста, поэтому цитата подтверждает факт."""

    def __init__(self,limit=1200):
        super().__init__();self.limit=limit;self.payloads=[];self.sizes=[];self.failed=[];self.answered=[];self.start=''

    def metric(self,error=None,streamed=0):
        metric=dict(mock=True,output_limit_tokens=2048,stream_limits={'bytes':2048*STREAM_BYTES_PER_TOKEN+STREAM_BYTE_SLACK},duration_ms=4.0,stream_bytes=streamed)
        if error:metric.update(status='error',error_code=error)
        self.metrics.append(metric);return metric

    async def stream(self,system,user,**kwargs):
        payload=json.loads(user);size=sum(len(p['text']) for p in payload['pages'])
        self.payloads.append(payload);self.sizes.append(size)
        if size>self.limit:
            self.failed.append(size);self.metric('stream_byte_limit',size)
            cap=2048*STREAM_BYTES_PER_TOKEN+STREAM_BYTE_SLACK
            raise CloudUnavailable('Ответ модели превысил локальный потолок байтов ('+str(cap)+'). Поток оборван.',error_code='stream_byte_limit',limit_bytes=cap)
        self.answered.append(size);self.metric(streamed=size)
        page=payload['pages'][0];name=re.search(r'Дом\d+',page['text']).group()
        self.start=self.start or name
        result=dict(title='Книга',ruleset='dnd2014',start=self.start,entities=[dict(
            key='house'+name[3:],kind='location',data=dict(name=name),evidence=[dict(source_span=re.search(r'\[(S\d+)\]',page['text'])[1])])])
        yield json.dumps(result,ensure_ascii=False)


class NearLimitFixture(SectionFixture):
    """Верный ответ, но поток занял 80 % потолка: секцию надо делить заново."""

    def __init__(self):
        super().__init__(limit=10**9);self.first=True

    async def stream(self,system,user,**kwargs):
        payload=json.loads(user);size=sum(len(p['text']) for p in payload['pages'])
        self.payloads.append(payload);self.sizes.append(size)
        near=self.first and size>4000
        if near:self.first=False
        self.answered.append(size);self.metric(streamed=int((2048*STREAM_BYTES_PER_TOKEN+STREAM_BYTE_SLACK)*.8) if near else size)
        page=payload['pages'][0];name=re.search(r'Дом\d+',page['text']).group()
        self.start=self.start or name
        result=dict(title='Книга',ruleset='dnd2014',start=self.start,entities=[dict(
            key='house'+name[3:],kind='location',data=dict(name=name),evidence=[dict(source_span=re.search(r'\[(S\d+)\]',page['text'])[1])])])
        yield json.dumps(result,ensure_ascii=False)


def test_answer_near_the_cap_stays_whole_and_the_cap_is_not_raised(tmp_path,monkeypatch):
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    llm=NearLimitFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book())
        started=client.post(url+'/prepare',json=dict(request_id='near',ruleset='dnd2014',consent_to_send=True)).json()
        status=terminal(client,started['id'])
        assert status['ready'] and status['done']==status['total']>=2,status
        journal=status['structure']
        # Верный ответ, занявший 80 % потолка, НЕ делится: поток состоит в
        # основном из SSE-обрамления, и «близко к стене» ≠ «не влезло». Дети
        # появляются только по факту отказа, иначе здоровый оплаченный ответ
        # оплачивается второй раз половинами.
        assert journal['split_sections']==0,journal
        assert journal['byte_limit']==2048*STREAM_BYTES_PER_TOKEN+STREAM_BYTE_SLACK  # потолок не тронут
        assert journal['min_headroom']>=0.15,journal
        with client.app.state.preparation.connection() as db:
            parts=db.execute('SELECT count(*) FROM creation_parts').fetchone()[0]
        assert parts==status['total'],parts


class BrokenBigFixture(SectionFixture):
    """Большая секция отвечает битым JSON, половина — годным: дробление лечит формат."""

    async def stream(self,system,user,**kwargs):
        payload=json.loads(user);size=sum(len(p['text']) for p in payload['pages'])
        self.payloads.append(payload);self.sizes.append(size);self.answered.append(size);self.metric(streamed=size)
        if size>3000:
            self.failed.append(size);yield '{"entities":[{"key":"x",'
            return
        page=payload['pages'][0];name=re.search(r'Дом\d+',page['text']).group()
        self.start=self.start or name
        yield json.dumps(dict(title='Книга',ruleset='dnd2014',start=self.start,entities=[dict(
            key='house'+name[3:],kind='location',data=dict(name=name),
            evidence=[dict(source_span=re.search(r'\[(S\d+)\]',page['text'])[1])])]),ensure_ascii=False)


def test_broken_format_splits_the_section_instead_of_losing_the_block(tmp_path,monkeypatch):
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    llm=BrokenBigFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book())
        started=client.post(url+'/prepare',json=dict(request_id='broken',ruleset='dnd2014',consent_to_send=True)).json()
        status=terminal(client,started['id'])
        assert status['ready'] and status['done']==status['total']>=2,status
        assert status['errors']==[]
        with client.app.state.preparation.connection() as db:
            reasons=[json.loads(row[0])[0]['reason'] for row in db.execute('SELECT diagnostic FROM creation_parts WHERE status="split"')]
        assert 'section_structure_error' in reasons,reasons


def upload(client,url,text):
    response=client.post(url+'/files',files={'file':('book.txt',text.encode())});response.raise_for_status()
    job=response.json()['files'][0]['job']['id'];deadline=time.monotonic()+60
    while client.get('/api/adventure/jobs/'+job).json()['status']!='complete':assert time.monotonic()<deadline;time.sleep(.02)


def test_byte_limit_splits_the_section_and_the_book_still_prepares(tmp_path,monkeypatch):
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    llm=SectionFixture(limit=2200)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book())
        started=client.post(url+'/prepare',json=dict(request_id='sections',ruleset='dnd2014',consent_to_send=True)).json()
        status=terminal(client,started['id'])
        assert status['ready'] and status['done']==status['total']>=3,status
        # Секции ушли маленькими, а потолок поймали и разделили, а не сдались.
        assert llm.failed and max(llm.failed)>2200 and llm.answered and max(llm.answered)<=2200
        journal=status['structure']
        assert journal['sections']==status['total'] and journal['answered_sections']==status['total']
        assert journal['split_sections']>=1 and journal['limit_hits']>=1
        assert journal['output_limit_tokens']==2048 and journal['byte_limit']==2048*STREAM_BYTES_PER_TOKEN+STREAM_BYTE_SLACK
        assert journal['min_headroom']>=0.3, journal
        assert all(entry['bytes']<=0.7*entry['byte_limit'] for entry in journal['responses'] if entry['status']=='validated')
        with client.app.state.preparation.connection() as db:
            split=[json.loads(row[0]) for row in db.execute('SELECT children FROM creation_parts WHERE status="split"')]
            assert split and all(len(children)==2 for children in split)


def test_section_that_cannot_be_split_names_the_book_fragment(tmp_path,monkeypatch):
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','4000')
    monkeypatch.setenv('SOLO_PREPARATION_MAX_SPLIT_DEPTH','2')
    llm=SectionFixture(limit=300)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=30))
        started=client.post(url+'/prepare',json=dict(request_id='toolarge',ruleset='dnd2014',consent_to_send=True)).json()
        status=terminal(client,started['id'])
        assert not status['ready'] and status['status'] in ('partial','failed')
        message=status['errors'][0]['message']
        assert 'Фрагмент книги' in message and 'не уложился в ответ' in message, status['errors']
        assert 'страница' in message
        # Дробление конечно: корень, две половины и четыре четверти — дальше
        # предел глубины (2), и отказ честно называет фрагмент.
        assert len(llm.payloads)==7 and len(llm.failed)==7,llm.sizes


def test_prose_is_not_a_chapter_heading():
    for prose in ('Часть добычи он оставил себе','Книга мёртвых лежала на столе',
                  'Part of the treasure was gone','Section of the wall had collapsed'):
        assert not starts_heading(prose),prose
    for heading in ('Глава 2. Башня','Часть I. Начало','Приложение Б. Бестиарий','Книга первая. Рассвет','Chapter Three'):
        assert starts_heading(heading),heading


def test_entity_budget_fits_the_contract_the_provider_receives():
    from solo.preparation_limits import limits
    from solo.source_extraction import SourceExtraction
    from solo.structured_output import strict_schema
    budget=limits()['entities_per_call']
    assert strict_schema(SourceExtraction.model_json_schema())['properties']['entries']['maxItems']>=budget


class AlwaysBrokenFixture(SectionFixture):
    """Битый JSON на любом размере: деление по формату не уходит вглубь."""

    def __init__(self):super().__init__(limit=10**9)

    async def stream(self,system,user,**kwargs):
        payload=json.loads(user);size=sum(len(p['text']) for p in payload['pages'])
        self.payloads.append(payload);self.sizes.append(size);self.answered.append(size);self.metric(streamed=size)
        yield '{"entities":[{"key":"x",'


def test_structure_error_splits_the_section_only_once(tmp_path,monkeypatch):
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    llm=AlwaysBrokenFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=30))
        started=client.post(url+'/prepare',json=dict(request_id='structure-once',ruleset='dnd2014',consent_to_send=True)).json()
        status=terminal(client,started['id'])
        assert not status['ready'] and status['status'] in ('partial','failed'),status
        # Корень и две половины: детерминированная ошибка формы не лечится
        # вглубь, и полное дерево до depth=3 оплачивалось бы зря.
        assert len(llm.payloads)==3,llm.sizes
        journal=status['structure']
        assert journal['split_sections']==1 and journal['limit_hits']==0,journal


class ServerErrorFixture(SectionFixture):
    """Провайдер отвечает 500: это не потолок ответа и не повод дробить."""

    def __init__(self):super().__init__(limit=10**9)

    async def stream(self,system,user,**kwargs):
        payload=json.loads(user);size=sum(len(p['text']) for p in payload['pages'])
        self.payloads.append(payload);self.sizes.append(size);self.failed.append(size)
        self.metric('http_500',size)
        raise CloudUnavailable('Провайдер ответил 500',error_code='http_500')


def test_provider_error_is_not_counted_as_a_byte_limit(tmp_path,monkeypatch):
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    llm=ServerErrorFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=2,paragraphs=30))
        started=client.post(url+'/prepare',json=dict(request_id='provider',ruleset='dnd2014',consent_to_send=True)).json()
        status=terminal(client,started['id'])
        assert not status['ready']
        journal=status['structure']
        assert journal['limit_hits']==0 and journal['split_sections']==0,journal


def test_world_import_accepts_the_size_preparation_allows(tmp_path):
    from solo.contracts import Hero
    from solo.storage import Store
    from solo.world import ScenarioPackage,import_package
    store=Store(tmp_path/'world.sqlite3');cid=store.create('Мир',Hero(name='Мирон'))['id']
    state=store.campaign(cid)['state']
    names=['Место '+str(i) for i in range(250)]
    before=len(state['locations'])
    package=ScenarioPackage.model_validate(dict(name='Большой мир',source='Книга',
        locations=[dict(name=n,description='Зал') for n in names],
        nodes=[dict(id='n'+str(i),name=n,coordinates=[i%40*2,i//40*2]) for i,n in enumerate(names)],routes=[],events=[]))
    import_package(state,package)
    assert len(state['locations'])==before+250



class ParallelFixture(PreparationFixture):
    """Считает, сколько секций реально читается одновременно."""

    def __init__(self,delay=.15):
        super().__init__(slow=delay);self.active=0;self.peak=0

    async def stream(self,system,user,**kwargs):
        self.active+=1;self.peak=max(self.peak,self.active)
        try:
            async for piece in super().stream(system,user,**kwargs):yield piece
        finally:self.active-=1


def test_sections_are_read_in_parallel_and_progress_is_written(tmp_path,monkeypatch):
    """r232: ожидание ответа модели не складывается в очередь, а прогресс
    пишется отдельно от человеческой фразы — по нему рисуется полоса."""
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','3000')
    monkeypatch.setenv('SOLO_PREPARATION_CONCURRENCY','3')
    llm=ParallelFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, D&D 2014');upload(client,url,book(chapters=4,paragraphs=40))
        started=client.post(url+'/prepare',json=dict(request_id='parallel',ruleset='dnd2014',consent_to_send=True)).json()
        status=terminal(client,started['id'])
        # Мир может вернуться на подтверждение сцены — это не провал разбора.
        # Проверяем именно то, о чём тест: все блоки прочитаны и читались разом.
        assert status['done']==status['total']>=2,status
        assert llm.peak>=2,llm.peak
        progress=status['progress']
        assert progress['concurrency']==3 and progress['total']==status['total'],progress
        assert progress['phase']=='assembling' and progress['failed']==0,progress
        assert progress['elapsed_s']>0,progress
