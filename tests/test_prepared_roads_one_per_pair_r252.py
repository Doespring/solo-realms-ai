"""r252: собранный мир запускается — между двумя местами одна дорога.

Живой прогон r250 (мир малой книги, 13 мест) упал на кнопке «Начать»:

    POST /api/prototype/campaigns → 409 {"detail":"Маршрут между узлами уже существует"}

В определении мира лежали оба направления одной двусторонней дороги
(`p11→p12` и `p12→p11`), а `import_package` (`solo/world.py:306`) сравнивает
маршруты как НЕупорядоченную пару: второй такой записи он не прощает и роняет
весь мир, а не одну дорогу. Источников перехода три — запись разбора, производная
пара (страница одного места называет другое) и предложение прозы, — и каждый мог
добавить своё направление той же пары.

Здесь три утверждения:

1. дубль решает одна функция `add_road`/`road_pair`, и `p11→p12` с `p12→p11`
   при `one_way=false` — для неё одна дорога;
2. собранный из книги мир, где обе стороны называют переход, содержит ровно один
   маршрут между этими местами, и он проходит `ScenarioPackage`/`import_package`;
3. кампания из такого мира открывается: `POST /api/prototype/campaigns` отвечает
   `200`, и стартовое место на месте.
"""
import json
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from solo.ai_roles import routerai_preset
from solo.app import create_app
from solo.prepared_world import add_road, road_pair
from solo.world import ScenarioPackage, WorldRoute, import_package
from nat20_builds import legal
from test_preparation_pipeline import PreparationFixture, terminal

# Книга печатает переход с обеих сторон: страница каждого места называет соседа
# («из Кузни группа попадёт в Галерею», «из Галереи группа попадёт в Кузню»).
# Разбор при этом называет дорогу один раз, своим направлением.
BOOK = '\n'.join([
    '## Кузня',
    'Кузня',
    'Кузня. Узкая лестница в 30 метров ведёт на галерею. Из Кузни группа попадёт в Галерею.',
    '',
    '## Галерея',
    'Галерея',
    'Галерея пуста. Из Галереи группа попадёт в Кузню через двор.',
]) + '\n'


def book(payload):
    page = payload['pages'][0]
    # Ссылка на переданный фрагмент: подстановка цитаты проверяет, что страница
    # печатает и «30 метров», и оба названия.
    span = re.match(r'\[(S\d+)\]', page['text']).group(1)
    proof = dict(source_span=span)
    entities = [dict(key=name, kind='location', data=dict(name=name), evidence=[proof])
                for name in ('Кузня', 'Галерея') if name in page['text']]
    if len(entities) == 2:
        entities.append(dict(key='the-way', kind='route', evidence=[proof],
                             data={'from': 'Кузня', 'to': 'Галерея',
                                   'description': 'Узкая лестница'}))
    return dict(title='Синтетическая книга', ruleset='dnd2014', start='Кузня', entities=entities)


def worlds(world):
    """Одна дорога на пару мест — то, чего требует `import_package`."""
    return [road_pair(route) for route in world['routes']]


@pytest.fixture()
def prepared(tmp_path):
    llm = PreparationFixture(book)
    client = TestClient(create_app(tmp_path / 'solo.sqlite3', llm, experimental=True))
    client.__enter__()
    draft = client.post('/api/creation/drafts').json()
    url = '/api/creation/drafts/' + draft['id']
    client.put(url, json=dict(description='Синтетическая книга, D&D 2014',
                              revision=draft['revision'])).raise_for_status()
    client.post(url + '/files', files={'file': ('kniga.txt', BOOK.encode())}).raise_for_status()
    end = time.monotonic() + 30
    while any(f['job']['status'] != 'complete' for f in client.get(url).json()['files']):
        assert time.monotonic() < end
        time.sleep(.02)
    started = client.post(url + '/prepare', json=dict(request_id='r252', world_structure=True,
                                                      ruleset='dnd2014', consent_to_send=True))
    assert started.status_code == 200, started.text
    run = started.json()
    status = terminal(client, run['id'])
    assert status['ready'], json.dumps(status, ensure_ascii=False)
    result = json.loads(client.app.state.preparation_pipeline.raw(run['id'])['result'])
    world = (result.get('full_definition') or result['definition'])['world']
    yield client, draft['id'], url, run['id'], result, world
    client.__exit__(None, None, None)


def test_two_sides_of_one_road_are_one_road_for_the_helper():
    """`p11→p12` и `p12→p11` при `one_way=false` — одна дорога."""
    first = WorldRoute(**{'from': 'p11', 'to': 'p12', 'meters': 10, 'minutes': 1, 'description': 'Путь'})
    second = WorldRoute(**{'from': 'p12', 'to': 'p11', 'meters': 10, 'minutes': 1, 'description': 'Дорога'})
    assert road_pair(first) == road_pair(second) == frozenset({'p11', 'p12'})
    roads = []
    assert add_road(roads, first) is True
    assert add_road(roads, second) is False
    assert [route.from_ for route in roads] == ['p11']
    # Разные пары и петли не путаются: петля — не дорога.
    assert add_road(roads, WorldRoute(**{'from': 'p12', 'to': 'p13', 'meters': 10, 'minutes': 1,
                                         'description': 'Путь'})) is True
    assert add_road(roads, WorldRoute(**{'from': 'p12', 'to': 'p12', 'meters': 10, 'minutes': 1,
                                         'description': 'Путь'})) is False
    # Односторонняя дорога и её обратная сторона — тоже одна пара, и вторая
    # запись делает её проходимой в обе стороны, а не теряет направление.
    one_way = [WorldRoute(**{'from': 'p20', 'to': 'p21', 'meters': 10, 'minutes': 1,
                             'description': 'Путь', 'one_way': True})]
    assert add_road(one_way, WorldRoute(**{'from': 'p21', 'to': 'p20', 'meters': 10, 'minutes': 1,
                                           'description': 'Путь'})) is False
    assert len(one_way) == 1 and one_way[0].one_way is False, one_way
    # Две односторонние записи в одну сторону остаются односторонними: обратный
    # путь книга не называет, и он не выдумывается.
    forward = [WorldRoute(**{'from': 'p30', 'to': 'p31', 'meters': 10, 'minutes': 1,
                             'description': 'Путь', 'one_way': True})]
    assert add_road(forward, WorldRoute(**{'from': 'p30', 'to': 'p31', 'meters': 10, 'minutes': 1,
                                           'description': 'Путь', 'one_way': True})) is False
    assert forward[0].one_way is True, forward


def test_a_book_names_the_road_from_both_sides_and_the_world_gets_one(prepared):
    """Один маршрут между местами, и он тот, который напечатан разбором."""
    _client, _draft, _url, _run, result, world = prepared
    pairs = worlds(world)
    assert len(pairs) == len(set(pairs)), world['routes']
    assert pairs == [frozenset({'p0', 'p1'})], world['routes']
    route = world['routes'][0]
    assert route['one_way'] is False, route
    # Между двумя местами дорога одна, и она та, которую называет разбор:
    # производная пара ушла бы направлением p1→p0 (её пара сортируется первой).
    assert (route['from'], route['to']) == ('p0', 'p1'), route
    steps = [note for note in result['annotations']
             if note.get('kind') == 'route' and note.get('field') == 'step']
    assert [note['key'] for note in steps] == ['the-way'], result['annotations']
    # Улики не теряются: пара уже занята записью разбора, поэтому производная
    # пара и проза помечены `joined=False` — они названы на странице, но второй
    # дороги не добавляют (иначе условие «если…» исчезает из мира молча).
    for field in ('derived', 'prose'):
        notes = [note for note in result['annotations']
                 if note.get('kind') == 'route' and note.get('field') == field]
        assert notes and all(note['joined'] is False for note in notes), (field, result['annotations'])
    assert all(note['sentence'] for note in result['annotations']
               if note.get('field') == 'prose'), result['annotations']


def test_the_assembled_world_is_imported_into_a_campaign_state(prepared):
    """`ScenarioPackage` этого мира проходит `import_package`, а не 409."""
    _client, _draft, _url, _run, result, world = prepared
    package = ScenarioPackage(name='Синтетическая книга', source='campaign-prepare-v1',
                              locations=world['locations'], nodes=world['nodes'],
                              routes=world['routes'], events=result['definition']['events'])
    state = dict(locations=[], location='')
    import_package(state, package)
    assert [place['name'] for place in state['locations']] == ['Кузня', 'Галерея']
    assert {frozenset((r['from'], r['to'])) for r in state['geography']['routes']} == {frozenset({'p0', 'p1'})}


def test_the_campaign_opens_from_the_assembled_world(prepared):
    """Кнопка «Начать»: подтверждение мира и создание кампании отвечают 200."""
    client, draft, url, run_id, _result, _world = prepared
    readiness = client.get(url + '/world-readiness').json()
    assert readiness['world_ready'], readiness
    confirmed = client.post(url + '/confirm-world', json=dict(expected_revision=readiness['revision'],
        run_id=readiness['run_id'], confirm=True))
    assert confirmed.status_code == 200, confirmed.text[:800]
    launched = client.post('/api/prototype/campaigns', json=dict(
        name='Рив', request_id='r252-start', build=legal(class_slug='fighter', species_slug='human', level=1),
        adventure='prepared:' + draft, prepared_spells=[], encounter_monsters=[],
        description='Синтетическая книга, D&D 2014', mechanics_ruleset='dnd2024',
        rules_confirmed=True, hero_confirmed=True, background_confirmed=True,
        hero_background='Ищет лестницу', adventure_revision=client.app.state.preparation.get(draft)['revision']))
    assert launched.status_code == 200, launched.text[:800]
    state = client.app.state.store.campaign(launched.json()['id'])['state']
    assert state['locations'], state['locations']
    assert state['locations'][0]['name'] == 'Кузня', state['locations'][0]['name']
    assert state['location'] == 'Кузня', state['location']
