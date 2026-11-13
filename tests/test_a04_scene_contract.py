"""A04 (F07+F08+F13): сцена — открытые факты места, осмотр не требует собеседника.

Аудит 08.11. Три дефекта, которые здесь закреплены сторожем:

* F07 — «Осматриваюсь» возвращал страницу книги: заголовок эпизода, обзор
  приключения и планы ведущего, а тот же длинный текст стоял под историей как
  статус. Проверяется контракт `public_scene.scene_contract`: что напечатала
  книга, что знает только Мастер, что видит игрок и короткая служебная строка —
  четыре разных текста, и в сцену игрока идут только открытые предложения.
* F08 — «что вокруг, какие выходы, кто присутствует?» уходил в разговор и
  получал «Здесь нет доступного собеседника»; одна часть составной реплики
  отменяла весь ход.
* F13 — вне боя единственной целью числился сам герой.

Модель синтетическая (`MockLLM` с подменённым `narrate_scene`), движок — только
локальный пакет: ни одного платного вызова.
"""
import asyncio
import json
import re
import socket
from types import SimpleNamespace

import pytest

from solo.app import create_app
from solo.contracts import Hero
from solo.llm import MockLLM
from solo.prototype_gm import present_intent
from solo import public_scene, scene_intents
from fixtures.hoard_pages import PAGES

SERVICE = re.compile(r'\b(?:эпизод\w*|совет\w*|сесси\w*)\b', re.I)

#: Страница книги как её печатает издание: заголовок эпизода, проза места и
#: половина ведущего (обзор приключения, планы, планы совета, план сессии).
BOOK = '\n'.join([
    'Эпизод 1: Гринест в огне',
    '6',
    PAGES[8],
    'Обзор Приключения',
    'В этом эпизоде герои должны понять, что на город напал Культ Дракона.',
    'Планы совета: старейшины решают, оборонять ли стены или уходить.',
    'Для этой сессии Мастер держит дракона за кадром и не показывает его раньше третьей сцены.',
])

SCENE = 'Огонь в очаге, запах хвои. Трактирщик ждёт у стойки.'
FACT = 'На стене висит карта тракта.'


# --- контракт сцены -------------------------------------------------------

def test_book_page_becomes_a_public_scene_without_the_masters_half():
    contract = public_scene.scene_contract(dict(name='Гринест', description=BOOK), source_text=BOOK)
    assert set(contract) == {'source_text', 'private_facts', 'public_scene', 'runtime_status'}, contract
    # Игроку — проза места, а не единица книги и не план ведущего.
    assert 'Гринест' in contract['public_scene']
    assert SERVICE.findall(contract['public_scene']) == [], contract['public_scene']
    assert 'Эпизод 1' not in contract['public_scene']
    # Страница книги сохранена отдельно: её читают для проверки, а не игроку.
    assert contract['source_text'] == BOOK
    # Половина Мастера названа тайной, а не сценой.
    assert any('совет' in row.casefold() for row in contract['private_facts']), contract['private_facts']
    assert all('совет' not in row.casefold() for row in [contract['public_scene']])


def test_status_is_one_short_line_and_does_not_repeat_the_scene():
    contract = public_scene.scene_contract(dict(name='Гринест', description=BOOK))
    status = contract['runtime_status']
    assert 0 < len(status) <= public_scene.STATUS_LIMIT, status
    assert status != contract['public_scene']
    # Короткая сцена не дублируется в статусе: статус — служебная строка места.
    short = public_scene.scene_contract(dict(name='Таверна', description=SCENE))
    assert short['runtime_status'] != short['public_scene'], short
    assert short['public_scene'] not in short['runtime_status'], short['runtime_status']
    assert 'Таверна' in short['runtime_status'], short['runtime_status']
    # Длинная сцена не «проглатывается»: строка обрывается по границе слова.
    long = public_scene.status_line('слово ' * 200)
    assert len(long) <= public_scene.STATUS_LIMIT and long.endswith('…'), long


def test_narrator_cannot_smuggle_the_book_back_into_the_scene():
    """Рассказчик повторяет книгу — в сцену игрока служебная фраза не проходит."""
    kept = public_scene.for_player(BOOK, fallback='Гринест.')
    assert SERVICE.findall(kept) == [], kept
    assert 'Гринест' in kept
    # Пустой ответ не превращается в пустую сцену: остаётся запасное название.
    assert public_scene.for_player('Планы совета.', fallback='Гринест') == 'Гринест'


# --- разбор реплики на части ---------------------------------------------

@pytest.mark.parametrize('text, expected', [
    ('Осматриваюсь', ['observe']),
    ('что вокруг, какие выходы, кто присутствует?', ['observe', 'observe', 'observe']),
    ('Говорю с Ириной', ['talk']),
    ('Скажи, что вокруг?', ['observe']),
    ('Спрашиваю Мастера о правилах', ['ask_master']),
    ('Осматриваюсь и спрашиваю трактирщика о драконе', ['observe', 'talk']),
])
def test_a_reply_is_split_into_separate_intents(text, expected):
    people = [dict(id='irina', name='Ирина')]
    assert [item['kind'] for item in scene_intents.plan(text, people)] == expected, scene_intents.plan(text, people)


# --- present_intent: сцена без служебного текста и без ложного отказа -----

class _Scope:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _presenter(tmp_path, answer):
    """`present_intent` с синтетическим рассказчиком: ни одного платного вызова."""
    app = create_app(tmp_path / 'solo.sqlite3', MockLLM())
    store = app.state.store
    cid = store.create('Сцена', Hero(name='Мирон', hp=12, max_hp=12))['id']
    seen = {}
    llm = MockLLM()

    async def narrate(system, packet, **kwargs):
        seen.update(system=system, packet=json.loads(packet))
        llm.last_narrative = dict(description=answer, dialogue=[], proposals=[])
        llm.last_narrative_policy = 'model_artistic'
        if False:
            yield ''

    llm.narrate_scene = narrate
    compiler = SimpleNamespace(engine_context=lambda *a, **k: dict(memories=[]))
    game = SimpleNamespace(llm=llm, model_scope=lambda *a, **k: _Scope(), store=store, compiler=compiler)
    return game, store, cid, seen


def _present(game, store, cid, *, text, status, target='', scene=None):
    scene = scene or dict(name='Таверна «Последний огонь»', description=SCENE, facts=[FACT], objects=[], npc=[])
    context = dict(scene=scene, memory_context=dict(memories=[]), game_snapshot=dict(actors=[]))
    intent = SimpleNamespace(target=target, status=status)
    return asyncio.run(present_intent(game, store.campaign(cid), 'a04-' + status, text, intent, context,
                                      utterance=text))


def test_observation_in_an_empty_scene_is_answered_not_refused(tmp_path):
    game, store, cid, seen = _presenter(tmp_path, SCENE)
    result = _present(game, store, cid, text='Осматриваюсь', status='observe')
    assert result['status'] == 'observation', result
    assert 'собеседник' not in result['narrative'].casefold(), result['narrative']
    assert 'не с кем' not in result['narrative'].casefold(), result['narrative']
    assert SCENE.split('.')[0] in result['narrative'], result['narrative']
    # Пакет рассказчика — открытые предложения места, а не страница книги.
    assert SERVICE.findall(seen['packet']['atmosphere']) == []


def test_a_scene_question_is_not_a_conversation(tmp_path):
    """F08: «что вокруг, какие выходы, кто присутствует?» — осмотр, не разговор."""
    game, store, cid, seen = _presenter(tmp_path, SCENE)
    result = _present(game, store, cid, text='что вокруг, какие выходы, кто присутствует?',
                      status='conversation')
    assert result['status'] == 'observation', result
    assert 'нет доступного собеседника' not in result['narrative'].casefold(), result['narrative']
    assert 'не с кем' not in result['narrative'].casefold(), result['narrative']


def test_a_composite_reply_keeps_the_worked_part_and_names_the_rest(tmp_path):
    game, store, cid, seen = _presenter(tmp_path, SCENE)
    result = _present(game, store, cid, text='Осматриваюсь и спрашиваю трактирщика о драконе',
                      status='conversation')
    narrative = result['narrative']
    assert result['status'] == 'observation', result
    # Выполненная часть видна: это осмотр, а не отказ всему ходу.
    assert SCENE.split('.')[0] in narrative, narrative
    # Отклонённая часть названа по-русски и не отменяет осмотр.
    assert 'Не выполнено: «спрашиваю трактирщика о драконе» — здесь не с кем говорить.' in narrative, narrative
    # Это не только ответ клиенту: та же строка лежит в журнале, иначе лента
    # покажет осмотр без объяснения, почему разговор не состоялся.
    stored = store.history(cid)[-1]['results'][0]
    assert 'Не выполнено' in stored['text']
    # Журнал хранит слово контракта ответа, а не слово интерпретатора.
    assert stored['kind'] == 'observation', stored


def test_the_book_page_never_reaches_the_player_on_the_first_turn(tmp_path):
    """Рассказчик повторяет страницу книги — игрок получает сцену, а не книгу."""
    game, store, cid, seen = _presenter(tmp_path, BOOK)
    scene = dict(name='Гринест', description=BOOK, facts=[], objects=[], npc=[])
    result = _present(game, store, cid, text='Осматриваюсь', status='observe', scene=scene)
    narrative = result['narrative']
    assert SERVICE.findall(narrative) == [], narrative
    assert 'Эпизод 1' not in narrative, narrative
    # Отказ валидатора назван честно: это не сбой провайдера.
    assert result['narrative_policy'] == 'engine_rejected', result
    assert result['narrative_reason'] == 'rejected_claim', result


def test_a_conversation_with_a_named_person_still_happens(tmp_path):
    """Проверка не запрещает разговор: названный человек в сцене — обычный ход."""
    game, store, cid, seen = _presenter(tmp_path, 'Ирина: «Брат ушёл к башне».')
    scene = dict(name='Таверна', description=SCENE, facts=[FACT], objects=[],
                 npc=[dict(id='irina', name='Ирина', known='Брат ушёл к башне.')])
    result = _present(game, store, cid, text='Говорю с Ириной', status='conversation',
                      target='Ирина', scene=scene)
    assert result['status'] == 'conversation', result
    assert 'Не выполнено' not in result['narrative'], result['narrative']


# --- F13: себя в целях нет ------------------------------------------------

@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv('SOLO_TEST', '1')
    connect = socket.socket.connect

    def local(sock, address):
        if address[0] in ('127.0.0.1', '::1'):
            return connect(sock, address)
        raise AssertionError('External calls are forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', local)


def test_the_hero_is_not_a_target_out_of_combat(tmp_path, offline):
    from solo.nat20_api import Nat20GameEngine
    from solo.storage import Store
    store = Store(tmp_path / 'targets.sqlite3')
    engine = Nat20GameEngine(store)
    cid = engine.create_campaign('A04', definition_id='fighter-standard-array-2024-v1', seed=1)['id']
    capabilities = engine.capabilities(store.campaign(cid))
    assert 'hero' not in capabilities['targets'], capabilities['targets']
    assert isinstance(capabilities['targets'], list)


# --- сквозной путь: кампания из книги и первый ход -----------------------

import hashlib  # noqa: E402
import uuid  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from solo.adventure_library import AUTO_SPLIT_KEY  # noqa: E402
from solo.preview_mock import PreviewMock  # noqa: E402

DOCUMENT = hashlib.sha256(b'klad-korolevy-drakonov-fixture').hexdigest()
CHAPTERS = [(8, 'Эпизод 1: Гринест в огне'), (9, 'Эпизод 1: Гринест в огне'),
            (16, 'Эпизод 2: Лагерь налетчиков'), (19, 'Эпизод 2: Лагерь налетчиков'),
            (22, 'Эпизод 2: Лагерь налетчиков')]


class MisreadingMock(PreviewMock):
    """Модель, которая повторила ошибку аудита: осмотр назван разговором."""

    def __init__(self):
        super().__init__()
        self.misreads = 0

    def scripted_intent(self, text):
        if 'кто присутствует' in text.casefold():
            self.misreads += 1
            return dict(status='conversation', target='')
        return super().scripted_intent(text)


def app(tmp_path, llm=None):
    return TestClient(create_app(tmp_path / 'solo.sqlite3', llm or PreviewMock(), experimental=True,
                                 offline_mock=True))


def book(library):
    bundle = library.create('Клад Королевы Драконов', {AUTO_SPLIT_KEY: True})['id']
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', max(PAGES)))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)', (bundle, DOCUMENT, 'book.pdf', 'book', ''))
        for number, text in PAGES.items():
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (DOCUMENT, number, str(number), text))
        for number, heading in CHAPTERS:
            db.execute('INSERT INTO candidates(id,document,page,kind,title,body) VALUES(?,?,?,?,?,?)',
                       (hashlib.sha256(heading.encode() + bytes([number % 256])).hexdigest()[:32],
                        DOCUMENT, number, 'chapter', heading, '{}'))
    return bundle


def prepared(client, bundle):
    draft = client.post('/api/preparation/drafts', json={}).json()
    body = draft['body']
    body.update(adventure_bundle=bundle, ruleset='dnd2014')
    saved = client.put('/api/preparation/drafts/' + draft['id'],
                       json={'body': body, 'revision': draft['revision']})
    assert saved.status_code == 200, saved.text
    world = client.post('/api/preparation/drafts/' + draft['id'] + '/world', json={}).json()
    assert world['bound'] is True, world
    return draft['id'], world


def load(client, draft_id):
    created = client.post('/api/prototype/campaigns', json=dict(
        name='Лира', request_id='a04-' + uuid.uuid4().hex[:8],
        build=client.get('/api/prototype/options').json()['default_build'],
        adventure='sandbox', preparation_draft=draft_id, encounter_monsters=[]))
    assert created.status_code == 200, created.text
    return created.json()['id']


def test_a_campaign_from_a_book_opens_on_a_public_scene_not_on_the_page(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.auto_split(bundle, DOCUMENT)
        draft_id, _world = prepared(client, bundle)
        campaign = load(client, draft_id)

        stored = client.app.state.store.campaign(campaign)
        place = stored['state']['locations'][0]
        assert place['name'] == 'Гринест'
        # Описание места — открытая проза, а не страница книги.
        assert SERVICE.findall(place['description']) == [], place['description']
        assert 'Эпизод 1' not in place['description'], place['description']
        # Страница книги сохранена отдельно: её читают, но игроку не показывают.
        assert SERVICE.search(stored['state']['preparation']['library']['source_text'])

        public = client.get('/api/campaigns/' + campaign).json()
        shown = public['state']['locations'][0]
        assert 0 < len(shown['status']) <= public_scene.STATUS_LIMIT, shown['status']
        assert shown['status'] != shown['description']
        assert shown['public_scene'] not in shown['status'], shown['status']
        assert SERVICE.findall(shown['public_scene']) == [], shown['public_scene']

        # Первая реплика Мастера в ленте — сцена, а не страница.
        setup = next(row for row in public['history'] if str(row.get('request_id')) == 'prepared-world')
        opening = setup['results'][0]['text']
        assert SERVICE.findall(opening) == [], opening
        assert 'Эпизод 1' not in opening, opening


def test_observation_and_a_misread_scene_question_are_both_answered(tmp_path):
    """Сквозной путь F08: разговор без собеседника не отменяет осмотр."""
    misreading = MisreadingMock()
    with app(tmp_path, misreading) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.auto_split(bundle, DOCUMENT)
        draft_id, _world = prepared(client, bundle)
        campaign = load(client, draft_id)

        def turn(text):
            version = client.app.state.store.campaign(campaign)['version']
            response = client.post('/api/campaigns/' + campaign + '/command-turn',
                                   json=dict(request_id='a04-' + uuid.uuid4().hex[:8], text=text,
                                             expected_version=version))
            assert response.status_code == 200, response.text
            return response.json()

        looked = turn('Осматриваюсь')
        assert looked['status'] == 'observation', looked
        assert 'собеседник' not in looked['narrative'].casefold(), looked['narrative']

        # Та же ошибка модели, что в аудите: осмотр разобран как разговор.
        questioned = turn('что вокруг, какие выходы, кто присутствует?')
        assert questioned['status'] == 'observation', questioned
        assert 'нет доступного собеседника' not in questioned['narrative'].casefold(), questioned['narrative']
        assert 'Гринест' in questioned['narrative'], questioned['narrative']
        # Ошибка модели действительно случилась: проверка не прошла по ошибке.
        assert misreading.misreads == 1, misreading.misreads
