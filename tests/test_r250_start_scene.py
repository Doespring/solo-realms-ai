"""r250: стартовое место получает текст книги, а не шаблон.

В r245 первый ход мастера упирался в шаблон: у стартового места не было
описания. Книга не печатает read-aloud, поэтому её проза уехала в `sealed_text`,
а в мир попало только название места. Проверяется:

1. собранный мир публикует видимые предложения книги как описание стартового
   места и первый факт — мастеру есть что подтвердить;
2. предложение со скрытым фактом в описание не попадает (фильтр, а не публикация
   всего абзаца);
3. уже собранный мир без текста сцены починяется при чтении, без вызовов модели
   (`publish_entry_scene`), и его отпечаток сцены пересчитывается;
4. мастер, добавивший факт вне пакета, по-прежнему получает отказ валидатора.

Модель синтетическая: ни одного платного вызова.
"""
import asyncio,hashlib,json,re,time
from contextlib import nullcontext

import pytest

from fastapi.testclient import TestClient

from solo.adventure_contracts import AdventureDefinition
from solo.ai_roles import routerai_preset
from solo.app import create_app
from solo.llm import MockLLM
from solo.narrative_grounding import CanonViolation, verify

SCENE='Ошеломляюще огромный город Мглистый охватывает почти весь слой.'
VISIBLE='Путник замечает, что город маячит на горизонте.'
HIDDEN='Под стеной спрятан тайник с ключом.'
TEMPLATE='Вы находитесь в месте «Город Мглистый». Что делает ваш герой?'


def book_text():
    """Место с прозой, но без read-aloud: ровно случай r245."""
    return '\n'.join([
        '## Глава 1',
        'Город Мглистый',
        SCENE,
        VISIBLE,
        HIDDEN,
        'Башня Мглы',
        'Башня Мглы стоит на краю города.',
    ])+'\n'


class SceneFixture(MockLLM):
    """Мир по секции: два места и дорога, описания — проза книги."""

    def __init__(self):
        super().__init__();self.payloads=[]

    def configured(self):return routerai_preset(),'fixture-only-never-sent'

    def scope(self,*args):return nullcontext()

    def contract_error(self,code):self.metrics.append(dict(status='invalid_contract',error=code))

    @staticmethod
    def chunks(pages):
        for page in pages:
            for marker,quote in re.findall(r'\[(S\d+)\]\s*(.*?)(?=\[S\d+\]|\Z)',page['text'],re.S):
                yield marker,quote

    async def stream(self,system,user,**kwargs):
        assert kwargs['mode']=='preparation_world';payload=json.loads(user);self.payloads.append(payload)
        self.metrics.append(dict(mode='preparation_world',mock=True,calls=1))
        await asyncio.sleep(0)
        pieces=list(self.chunks(payload['pages']))
        def cited(name):
            for marker,quote in pieces:
                if name in quote:return dict(source_span=marker)
            return None
        prose={'Город Мглистый':' '.join((SCENE,VISIBLE,HIDDEN)),
               'Башня Мглы':'Башня Мглы стоит на краю города.'}
        entities=[]
        for name,text in prose.items():
            proof=cited(name)
            if proof is None:continue
            entities.append(dict(key=name,kind='location',data=dict(name=name,description=text),
                evidence=[proof]))
        if cited('Город Мглистый') and cited('Башня Мглы'):
            entities.append(dict(key='road',kind='route',data=dict(**{'from':'Город Мглистый'},
                to='Башня Мглы',description='Дорога между местами'),evidence=[cited('Город Мглистый')]))
        value=dict(title='Синтетическая книга',ruleset='dnd2014',start='Город Мглистый',entities=entities)
        raw=json.dumps(value,ensure_ascii=False)
        for offset in range(0,len(raw),31):
            yield raw[offset:offset+31];await asyncio.sleep(0)


def prepared_run(client,run_id,deadline=90):
    end=time.monotonic()+deadline
    while time.monotonic()<end:
        view=client.get('/api/creation/runs/'+run_id).json()
        if view['status'] not in ('queued','running'):return view
        time.sleep(.02)
    raise AssertionError('Подготовка не завершилась')


def start_place(result):
    start=(result.get('start') or '').strip()
    for place in (result['definition'].get('world') or {}).get('locations') or []:
        if place.get('name')==start:return place
    raise AssertionError('стартового места нет в мире: '+start)


def test_start_scene_publishes_the_books_own_visible_text(tmp_path,monkeypatch):
    llm=SceneFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft=client.post('/api/creation/drafts').json();url='/api/creation/drafts/'+draft['id']
        client.put(url,json=dict(description='Книга без read-aloud',revision=draft['revision'])).raise_for_status()
        client.post(url+'/files',files={'file':('kniga.txt',book_text().encode())}).raise_for_status()
        end=time.monotonic()+30
        while any(f['job']['status']!='complete' for f in client.get(url).json()['files']):
            assert time.monotonic()<end;time.sleep(.02)
        started=client.post(url+'/prepare',json=dict(request_id='r250-scene',world_structure=True,
            ruleset='dnd2014',consent_to_send=True))
        assert started.status_code==200,started.text
        run=started.json()
        final=prepared_run(client,run['id'])
        assert final['status']=='ready',final
        result=json.loads(client.app.state.preparation_pipeline.raw(run['id'])['result'])
        place=start_place(result)
        # 1. Описание — проза книги, а не шаблон и не одно название.
        assert place['description']!=TEMPLATE,place['description']
        assert place['description'].rstrip('.')!='Город Мглистый',place['description']
        assert SCENE in place['description'],place['description']
        assert VISIBLE in place['description'],place['description']
        # 2. Скрытый факт остаётся закрытым.
        assert 'тайник' not in place['description'].casefold(),place['description']
        assert place['facts'],place
        assert all('тайник' not in str(fact).casefold() for fact in place['facts']),place['facts']
        # 3. Провенанс называет документ и страницу; если у мира есть отпечаток
        # сцены, он сходится с опубликованным текстом.
        stage=(result['definition'].get('entry_stage') or {})
        if stage:assert stage['opening_sha256']==hashlib.sha256(place['description'].encode()).hexdigest(),stage
        source=result['opening_source']
        assert source['text'] in ('printed-observable','book-text'),source
        assert source['document'] and source['page']>=1,source
        assert source['page_sha256']==hashlib.sha256(book_text().encode()).hexdigest(),source
        # 4. Игрок видит тот же текст на первом экране.
        readiness=client.get(url+'/world-readiness').json()
        assert readiness['world_ready'],readiness
        confirmed=client.post(url+'/confirm-world',json=dict(expected_revision=readiness['revision'],
            run_id=readiness['run_id'],confirm=True))
        assert confirmed.status_code==200,confirmed.text
        assert SCENE in client.app.state.preparation.get(draft['id'])['body']['opening']


def test_verified_page_feeds_the_scene_when_the_world_has_no_text():
    """Мир, у которого страница печатает абзац, а `sealed_text` пуст (r245)."""
    from solo.first_playable import publish_entry_scene
    from solo.prepared_world import page_paragraph
    page='\n'.join(['6','Синтетическая книга','Город Мглистый',SCENE,VISIBLE,
        'Сл 24 — проверка Мудрости на входе.',HIDDEN,'Башня Мглы',
        'Башня Мглы стоит на краю города.'])
    paragraph=page_paragraph(page,'Город Мглистый',['Башня Мглы'])
    joined=' '.join(paragraph)
    assert paragraph[0].startswith('Ошеломляюще огромный город Мглистый'),paragraph
    # Чужое место обрывает абзац: сцена не заканчивается его названием.
    assert 'Башня Мглы' not in joined,joined
    legacy=dict(start='Город Мглистый',ready=True,title='Мир без текста',definition=dict(
        id='page-fed',title='Мир без текста',ruleset='dnd2024',ruleset_version=1,reviewed=True,
        entry_stage=dict(policy='verified-entry-v1',document='b'*64,page=6,
            opening_sha256=hashlib.sha256('Город Мглистый.'.encode()).hexdigest(),
            boundary='Переход дальше закрыт.'),
        world=dict(name='Город Мглистый',source='fixture-book',
            locations=[dict(name='Город Мглистый',description='Город Мглистый.',facts=[],sealed_text='',
                objects=[],npc=[],interactions=[],known_to_hero=True),
                dict(name='Башня Мглы',description='',facts=[],sealed_text='',
                objects=[],npc=[],interactions=[],known_to_hero=False)],
            nodes=[],routes=[],events=[])))
    fixed=publish_entry_scene(legacy,page_text=page)
    place=start_place(fixed)
    assert SCENE in place['description'] and VISIBLE in place['description'],place['description']
    # Заметка мастеру и скрытый факт со страницы остаются закрытыми.
    assert 'Сл 24' not in place['description'],place['description']
    assert 'тайник' not in place['description'].casefold(),place['description']
    assert place['facts']==[SCENE],place['facts']
    AdventureDefinition.model_validate(fixed['definition'])


def test_observation_asks_the_narrator_to_restate_the_confirmed_scene(tmp_path):
    """Осмотр: мастеру велено пересказать сцену, а не сочинять свою.

    r250, живой прогон: сцена в мире появилась, но модель написала «Дис
    поднимается из мглы, как оттиск чужой воли…», валидатор это справедливо
    отбросил, и игрок снова увидел шаблон. Инструкция просит дословный пересказ
    atmosphere/confirmed; verify при этом не менялся.
    """
    import asyncio
    from types import SimpleNamespace
    from solo.app import create_app
    from solo.contracts import Hero
    from solo.prototype_gm import present_intent
    from solo.llm import MockLLM
    seen={}
    llm=MockLLM()
    async def narrate(system,packet,**kwargs):
        seen.update(system=system,packet=json.loads(packet))
        llm.last_narrative=dict(description=SCENE+' '+VISIBLE,dialogue=[],proposals=[])
        llm.last_narrative_policy='model_artistic'
        if False:yield ''
    llm.narrate_scene=narrate
    class Scope:
        def __enter__(self):return self
        def __exit__(self,*exc):return False
    app=create_app(tmp_path/'solo.sqlite3',MockLLM())
    store=app.state.store
    cid=store.create('Сцена',Hero(name='Мирон',hp=12,max_hp=12))['id']
    game=SimpleNamespace(llm=llm,model_scope=lambda *a,**k:Scope(),store=store)
    context=dict(scene=dict(name='Город Мглистый',description='Город Мглистый. '+SCENE+' '+VISIBLE,
            facts=[SCENE],objects=[],npc=[]),confirmed=[SCENE],
        memory_context=dict(memories=[]),game_snapshot=dict(actors=[]))
    result=asyncio.run(present_intent(game,store.campaign(cid),'r250-observe','Осматриваюсь',
        SimpleNamespace(target='',status='observe'),context))
    assert 'осматривается' in seen['system'] and 'atmosphere и confirmed' in seen['system'],seen['system']
    assert seen['packet']['atmosphere']=='Город Мглистый. '+SCENE+' '+VISIBLE
    assert seen['packet']['confirmed']==[SCENE]
    assert result['narrative_policy']=='engine_verified',result
    assert 'Нового ничего не заметно' not in result['narrative'],result['narrative']


def test_prepared_world_without_scene_text_is_repaired_on_read():
    """Мир, собранный до r250: описание — название, прозы в мире нет."""
    from solo.first_playable import publish_entry_scene
    page='\n'.join(['6','Синтетическая книга','Город Мглистый',SCENE,VISIBLE,'Спасательный круг'])
    legacy=dict(start='Город Мглистый',ready=True,title='Старый мир',definition=dict(
        id='legacy',title='Старый мир',ruleset='dnd2024',ruleset_version=1,reviewed=True,
        entry_stage=dict(policy='verified-entry-v1',document='a'*64,page=4,
            opening_sha256=hashlib.sha256('Город Мглистый.'.encode()).hexdigest(),
            boundary='Переход дальше закрыт.'),
        world=dict(name='Город Мглистый',source='fixture-book',
            locations=[dict(name='Город Мглистый',description='Город Мглистый.',facts=[],
            sealed_text=' '.join((SCENE,VISIBLE,HIDDEN)),objects=[],npc=[],interactions=[],known_to_hero=True)],
            nodes=[],routes=[],events=[])))
    before=json.loads(json.dumps(legacy))
    fixed=publish_entry_scene(legacy,page_text=page)
    place=start_place(fixed)
    assert SCENE in place['description'] and VISIBLE in place['description'],place['description']
    # Материал мастера из sealed_text в сцену не попадает, даже когда страница
    # его не дублирует: это служебное поле компилятора, а не текст для игрока.
    assert 'тайник' not in place['description'].casefold(),place['description']
    assert place['facts']==[SCENE],place['facts']
    assert fixed['definition']['entry_stage']['opening_sha256']==hashlib.sha256(place['description'].encode()).hexdigest()
    # Проверка входа сверяет отпечаток с текстом места: мир обязан быть валиден.
    AdventureDefinition.model_validate(fixed['definition'])
    # Чтение не меняет сохранённый прогон и повторный вызов ничего не добавляет.
    assert legacy==before
    assert publish_entry_scene(fixed)==fixed


def test_master_half_of_the_page_is_never_published():
    """Абзац кончается там, где начинается половина мастера."""
    from solo.first_playable import publish_entry_scene
    page='\n'.join(['6','Синтетическая книга','Город Мглистый',SCENE,VISIBLE,
        'Сокровище: 200 золотых в сундуке.','КД 16, 22 хита, урон 1к8+2.',
        'Путник входит в город через южные ворота.'])
    legacy=dict(start='Город Мглистый',ready=True,title='Мир гостиницы',definition=dict(
        id='gm-half',title='Мир гостиницы',ruleset='dnd2024',ruleset_version=1,reviewed=True,
        world=dict(name='Город Мглистый',source='fixture-book',
            locations=[dict(name='Город Мглистый',description='Город Мглистый.',facts=[],sealed_text='',
            objects=[],npc=[],interactions=[],known_to_hero=True)],nodes=[],routes=[],events=[])))
    place=start_place(publish_entry_scene(legacy,page_text=page))
    assert SCENE in place['description'],place['description']
    assert 'Сокровище' not in place['description'],place['description']
    assert 'КД' not in place['description'],place['description']
    # После блока характеристик идёт другая сцена, а не продолжение этой.
    assert 'южные ворота' not in place['description'],place['description']


def test_a_page_without_the_place_heading_is_reported_not_silently_skipped():
    """Страница без заголовка места: шаблон остаётся, но это видно в отчёте."""
    from solo.first_playable import publish_entry_scene
    legacy=dict(start='Город Мглистый',ready=True,title='Мир без заголовка',definition=dict(
        id='no-heading',title='Мир без заголовка',ruleset='dnd2024',ruleset_version=1,reviewed=True,
        world=dict(name='Город Мглистый',source='fixture-book',
            locations=[dict(name='Город Мглистый',description='Город Мглистый.',facts=[],sealed_text='',
            objects=[],npc=[],interactions=[],known_to_hero=True)],nodes=[],routes=[],events=[])))
    fixed=publish_entry_scene(legacy,page_text='Колонтитул\nВ город ведёт дорога.\nЕщё абзац.')
    assert start_place(fixed)['description']=='Город Мглистый.'
    assert fixed['opening_source']['text']=='unresolved',fixed['opening_source']
    # Входной объект не изменён.
    assert 'opening_source' not in legacy


def test_master_still_cannot_add_a_fact_outside_the_packet():
    """Граница: текст сцены появился, валидатор не ослаблен."""
    packet=dict(place='Город Мглистый',atmosphere='Город Мглистый. '+SCENE+' '+VISIBLE,
        confirmed=[SCENE],npc=[],memory=[],observation=True,outcomes=[])
    verify(dict(description=SCENE,dialogue=[],proposals=[]),packet)
    with pytest.raises(CanonViolation):
        verify(dict(description='В городе пахнет серой и гарью.',dialogue=[],proposals=[]),packet)
