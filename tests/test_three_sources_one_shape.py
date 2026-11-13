"""r134 · Три источника — одна форма приключения.

Раньше «три входа дают один результат» было проверено кусками: отдельно PDF,
отдельно разметка, отдельно JSON. Здесь один файл подаёт все три источника в
один пайплайн (`solo/preparation_pipeline.py` → `PreparedWorldValidator.compile`)
и сравнивает **форму** результата, а не текст: у книги, авторского сценария и
JSON-сценария содержимое своё, а контракт мира один.

Источники намеренно разные и все уже существуют в дереве:

* PDF — готовая фикстура `scripts/smoke_fixture.smoke_pdf_bytes()` (новый PDF не
  генерируется);
* TXT/MD — явная разметка `## Старт:` / `## Локация:` / `## NPC: Имя @ Место` /
  `## Дорога:` / `## Событие: ID` (`solo/preparation.py:text_structure`);
* JSON — сценарий по схеме `solo.world.ScenarioPackage`.

Модель — офлайн-мок `PreparationFixture` (`tests/test_preparation_pipeline.py`):
ни одного внешнего вызова. Мок ничего не выдумывает: каждое имя он обязан найти
в тексте страницы, иначе поднимает ошибку. Поэтому «одинаковая форма» — это
свойство пайплайна, а не ответа мока.
"""
import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'tests') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tests'))

from fastapi.testclient import TestClient  # noqa: E402

from solo.app import create_app  # noqa: E402
from solo.preparation import extract  # noqa: E402
from solo.world import ScenarioPackage  # noqa: E402
from scripts.smoke_fixture import (  # noqa: E402
    PDF_SMOKE_KNOWLEDGE, PDF_SMOKE_OPENING, smoke_pdf_bytes)
from test_preparation_pipeline import PreparationFixture, new, terminal  # noqa: E402

# --- Содержимое трёх источников -------------------------------------------
#
# PDF — англоязычная фикстура проекта, её текст менять нельзя. Поэтому форма
# сравнивается по контракту, а не по именам: у книги своё содержимое, у
# авторского сценария своё. Состав при этом одинаковый: 2 места, 1 NPC,
# 1 дорога, 1 проверка со Сл (у авторских источников ещё событие).

READ_ALOUD = ('Перед вами лесная дорога. У сторожевой башни ждёт проводница Ирина. '
             'Ирина — женщина в дорожном плаще. '
             'Сл 14 — проверка Мудрости (Внимательность). '
             'Через 30 минут над лесом поднимается буря.')

MARKUP = '''## Старт: Перекрёсток
Прочитайте игрокам: Перед вами лесная дорога. У сторожевой башни ждёт проводница Ирина. Ирина — женщина в дорожном плаще. Сл 14 — проверка Мудрости (Внимательность). Через 30 минут над лесом поднимается буря.
## Локация: Убежище
Тропа ведёт в сырое подземелье.
## NPC: Ирина @ Перекрёсток
Описание: Женщина в дорожном плаще.
## Дорога: Перекрёсток -> Убежище
Расстояние 100 метров, 3 минуты.
## Событие: storm
Через 30 минут над лесом поднимается буря.
'''

SCENARIO = {
    'name': 'Перекрёсток и убежище',
    'source': 'authored-scenario',
    'locations': [
        {'name': 'Перекрёсток', 'known_to_hero': True,
         'description': 'Прочитайте игрокам: ' + READ_ALOUD,
         'npc': [{'name': 'Ирина', 'character': 'Женщина в дорожном плаще.'}]},
        {'name': 'Убежище', 'description': ''}],
    'nodes': [
        {'id': 'source-location-0', 'name': 'Перекрёсток', 'coordinates': [2, 2], 'known': True},
        {'id': 'source-location-1', 'name': 'Убежище', 'coordinates': [4, 2], 'known': False}],
    'routes': [{'from': 'source-location-0', 'to': 'source-location-1',
                'meters': 100, 'minutes': 3, 'description': 'Расстояние 100 метров, 3 минуты.'}],
    'events': [{'id': 'storm', 'kind': 'notice', 'location': 'Убежище',
                'text': 'Через 30 минут над лесом поднимается буря.'}],
}

JSON_SOURCE = json.dumps(SCENARIO, ensure_ascii=False)

#: Проза без разметки и без имён: из неё структуру брать не из чего.
PROSE = ('Вечер был тихим, дорога уходила в лес. Кто-то шёл рядом и молчал, '
         'и не было ни названия места, ни имени спутника. ' * 4)

#: Что именно напечатано в каждом источнике. Мок берёт факты отсюда и обязан
#: найти каждый из них в тексте страницы — выдумывать ему нечего.
FACTS = {
    'pdf': dict(marker='Read aloud: You stand in the Forge.',
                start='Forge', other='Tower', npc='Dorn', npc_place='Forge', route='Path',
                check_quote='Path: Forge to Tower, 10 meters, 1 minute.',
                check=dict(key='loc:forge:ability:dex:1', ability='dexterity', skill='', dc=10),
                route_numbers=(10, 1), event=None),
    'md': dict(marker='## Старт: Перекрёсток',
               start='Перекрёсток', other='Убежище', npc='Ирина', npc_place='Перекрёсток', route='Дорога',
               check_quote='Сл 14 — проверка Мудрости (Внимательность).',
               check=dict(key='loc:perekrestok:skill:perception:1', ability='wisdom',
                          skill='perception', dc=14),
               route_numbers=(100, 3),
               event=dict(id='storm', after_minutes=30,
                          text='Через 30 минут над лесом поднимается буря.')),
    'json': dict(marker='"known_to_hero": true',
                 start='Перекрёсток', other='Убежище', npc='Ирина', npc_place='Перекрёсток', route='Дорога',
                 check_quote='Сл 14 — проверка Мудрости (Внимательность).',
                 check=dict(key='loc:perekrestok:skill:perception:1', ability='wisdom',
                            skill='perception', dc=14),
                 route_numbers=(100, 3),
                 event=dict(id='storm', after_minutes=30,
                            text='Через 30 минут над лесом поднимается буря.')),
}


def _page_text(payload):
    return '\n'.join(page['text'] for page in payload.get('pages', []))


def _facts_for(text):
    hits = [name for name, fact in FACTS.items() if fact['marker'] in text]
    assert len(hits) == 1, 'мок узнаёт только объявленные источники, а не угадывает: ' + repr(hits)
    return FACTS[hits[0]]


def proposal(payload):
    """Один контракт ответа для любого источника: только напечатанные факты."""
    text = _page_text(payload)
    fact = _facts_for(text)
    for name in (fact['start'], fact['other'], fact['npc']):
        assert name in text, 'мок не имеет права назвать то, чего нет на странице: ' + name
    span = re.search(r'\[(S\d+)\]', text)[1]
    proof = [dict(source_span=span)]
    document = payload['pages'][0]['document']
    page = payload['pages'][0]['page']
    check = {key: value for key, value in fact['check'].items() if key != 'key'}
    check.update(origin='source', trigger='напечатано в источнике',
                 source=dict(document=document, page=page, quote=fact['check_quote']))
    entities = [
        dict(key='start-place', kind='location',
             data={'name': fact['start'], 'checks': {fact['check']['key']: check},
                   'description': PDF_SMOKE_OPENING if fact is FACTS['pdf'] else READ_ALOUD},
             evidence=proof),
        dict(key='other-place', kind='location', data={'name': fact['other']}, evidence=proof),
        dict(key='person', kind='npc', location=fact['start'],
             data={'name': fact['npc'], 'character': 'a smith' if fact is FACTS['pdf']
                   else 'Ирина — женщина в дорожном плаще.',
                   'known': PDF_SMOKE_KNOWLEDGE if fact is FACTS['pdf'] else ''}, evidence=proof),
        dict(key='road', kind='route',
             data={'from': fact['start'], 'to': fact['other'],
                   'meters': fact['route_numbers'][0], 'minutes': fact['route_numbers'][1],
                   'description': 'напечатанная дорога'}, evidence=proof)]
    if fact['event']:
        entities.append(dict(key=fact['event']['id'], kind='event',
                             data={'id': fact['event']['id'], 'category': 'timed',
                                   'after_minutes': fact['event']['after_minutes'],
                                   'effects': [dict(kind='notice', text=fact['event']['text'],
                                                    public=True)]},
                             evidence=proof))
    return dict(title=fact['start'] + ' и ' + fact['other'], ruleset='dnd2014', start=fact['start'],
                entities=entities)


def fabricating(payload):
    """Мок, который выдумывает место: цитаты с таким текстом на странице нет."""
    page = payload['pages'][0]
    return dict(title='Выдумка', ruleset='dnd2014', start='Выдуманный город', entities=[
        dict(key='invented', kind='location', data={'name': 'Выдуманный город'},
             evidence=[dict(document=page['document'], page=page['page'],
                            quote='В городе ждут два NPC, которых нет в источнике.')])])


def prepare(client, url, request_id):
    response = client.post(url + '/prepare', json=dict(request_id=request_id, ruleset='dnd2014',
                                                       world_structure=True, consent_to_send=True))
    assert response.status_code == 200, response.text
    status = terminal(client, response.json()['id'])
    detail = json.loads(client.app.state.preparation_pipeline.raw(status['id'])['result'])
    return status, detail


def upload(client, url, name, raw):
    response = client.post(url + '/files', files={'file': (name, raw)})
    assert response.status_code == 200, response.text
    import time
    job = response.json()['files'][0]['job']['id']
    deadline = time.monotonic() + 20
    while client.get('/api/adventure/jobs/' + job).json()['status'] != 'complete':
        assert time.monotonic() < deadline, 'извлечение источника не завершилось'
        time.sleep(0.02)


def shape_of(detail):
    """Форма мира: ключи, поля коллекций и происхождение. Без текста и имён."""
    definition = detail['definition']
    world = definition['world'] or {}
    locations = world.get('locations') or []

    def fields(rows):
        return sorted({key for row in rows for key in row})

    kinds = sorted({row['kind'] for row in detail['provenance'].values()})
    evidence = [quote for row in detail['provenance'].values() for quote in row.get('evidence') or []]
    return dict(
        result=sorted(detail),
        definition=sorted(definition),
        world=sorted(world),
        location=fields(locations),
        npc=fields([person for place in locations for person in place.get('npc') or []]),
        route=fields([route for route in world.get('routes') or []]),
        event=fields(definition.get('events') or []),
        check=sorted({key for place in locations for value in (place.get('checks') or {}).values()
                      for key in value}),
        provenance=fields(list(detail['provenance'].values())),
        evidence=sorted({key for quote in evidence for key in quote}),
        kinds=kinds,
    )


def compiled(name):
    """Один источник → prepared world. Возвращает (status, detail, model)."""
    model = PreparationFixture(proposal)
    with TestClient(create_app(pathlib.Path(_tmpdir(name)) / 'solo.sqlite3', model)) as client:
        draft, url = new(client, 'Источник: ' + name + ', D&D 2014')
        if name == 'pdf':
            upload(client, url, 'forge-and-tower.pdf', smoke_pdf_bytes())
        elif name == 'md':
            upload(client, url, 'scenario.md', MARKUP.encode())
        else:
            ScenarioPackage.model_validate(SCENARIO)
            upload(client, url, 'scenario.json', JSON_SOURCE.encode())
        status, detail = prepare(client, url, name)
        return status, detail, model


_TMP = {}


def _tmpdir(name):
    return _TMP[name]


@pytest.fixture(scope='module', autouse=True)
def temp_dirs(tmp_path_factory):
    for name in ('pdf', 'md', 'json', 'prose', 'fabrication'):
        _TMP[name] = tmp_path_factory.mktemp(name + '-profile')
    return _TMP


@pytest.fixture(scope='module')
def worlds():
    return {name: compiled(name) for name in ('pdf', 'md', 'json')}


def test_every_source_reaches_a_ready_prepared_world(worlds):
    for name, (status, detail, _) in worlds.items():
        assert status['ready'], (name, status['message'])
        assert status['preparation_mode'] == 'world_structure'
        world = detail['definition']['world']
        assert detail['start'] and world['locations']
        start = next(place for place in world['locations'] if place['name'] == detail['start'])
        assert start['description'].strip(), name + ': у стартового места нет открытого описания'
        assert world['routes'], name + ': дорога не доехала до мира'


def test_the_three_sources_share_one_shape(worlds):
    shapes = {name: shape_of(detail) for name, (_, detail, _) in worlds.items()}
    for field in ('result', 'definition', 'world', 'location', 'npc', 'route', 'provenance',
                  'evidence', 'check'):
        values = {name: tuple(shape[field]) for name, shape in shapes.items()}
        assert len(set(values.values())) == 1, (field, json.dumps(values, ensure_ascii=False))
    # Набор полей события проверяется на тех источниках, где событие напечатано,
    # а форма события у них одна.
    events = {name: tuple(shape['event']) for name, shape in shapes.items() if shape['event']}
    assert len(set(events.values())) == 1 and len(events) == 2, events
    for name, shape in shapes.items():
        print('\n' + name.upper(), 'ключи результата:', ', '.join(shape['result']))
        print(name.upper(), 'сущности:', ', '.join(shape['kinds']),
              '| поля места:', ', '.join(shape['location']),
              '| поля события:', ', '.join(shape['event']) or '—',
              '| поля проверки:', ', '.join(shape['check']))
    print('\nСОВПАЛО: result, definition, world, поля места/NPC/дороги/события/проверки, '
          'происхождение (document+page+quote)')
    print('РАЗОШЛОСЬ: только состав содержимого — в PDF-фикстуре не напечатано событие')


def test_each_accepted_entity_keeps_its_page_and_quote(worlds):
    for name, (_, detail, _) in worlds.items():
        for key, row in detail['provenance'].items():
            assert row['evidence'], name + '/' + key + ': сущность без происхождения'
            for quote in row['evidence']:
                assert re.fullmatch(r'[0-9a-f]{64}', quote['document'])
                assert isinstance(quote['page'], int) and quote['page'] >= 1
                assert len(quote['quote']) >= 3


def test_checks_with_their_dc_survive_in_all_three(worlds):
    dcs = {}
    for name, (_, detail, _) in worlds.items():
        checks = [value for place in detail['definition']['world']['locations']
                  for value in (place.get('checks') or {}).values()]
        assert checks, name + ': ни одной проверки из источника'
        assert all(check['origin'] == 'source' and check['source']['page'] >= 1 for check in checks)
        dcs[name] = min(check['dc'] for check in checks)
    assert set(dcs) == {'pdf', 'md', 'json'} and all(value >= 1 for value in dcs.values())


def test_markup_and_json_compile_to_the_same_world(worlds):
    """Одинаковый сценарий в разметке и в JSON — один и тот же мир, без расхождений."""
    stripped = {}
    for name in ('md', 'json'):
        definition = json.loads(json.dumps(worlds[name][1]['definition'], ensure_ascii=False))
        definition.pop('id')
        for event in definition.get('events') or []:  # у каждого источника свой sha
            if isinstance(event.get('source'), dict):
                event['source'] = dict(event['source'], document='*')
        for place in definition['world']['locations']:
            checks = place.get('checks') or {}
            for check in checks.values():  # документ у этих двух источников разный по sha
                check['source'] = dict(check['source'], document='*')
            for person in place.get('npc') or []:
                person.pop('id', None)
        definition['world'].pop('source')
        stripped[name] = definition
    assert stripped['md'] == stripped['json']


def test_prose_without_markup_invents_nothing():
    # Локальный разбор: текст проиндексирован, структура не выдумана.
    _, quality, package = extract('prose.md', PROSE.encode())
    assert quality['structure'] == 'text_index' and package is None
    assert 'locations' not in quality and 'npc' not in quality
    assert 'разметки' in ' '.join(quality['warnings'])

    model = PreparationFixture(fabricating)
    with TestClient(create_app(_TMP['prose'] / 'solo.sqlite3', model)) as client:
        draft, url = new(client, 'Просто проза, D&D 2014')
        upload(client, url, 'prose.md', PROSE.encode())
        status, detail = prepare(client, url, 'prose')
    assert not status['ready']
    assert detail['quarantine'] and detail['quarantine'][0]['key'] == 'invented'
    assert not (detail['definition'].get('world') or {}).get('locations')


def test_no_provider_call_and_no_invention_in_the_mock(worlds):
    for name, (_, _, model) in worlds.items():
        assert len(model.payloads) == 1, name + ': подготовка обязана стоить один вызов'
        assert all(entry.get('mock') for entry in model.metrics), name
