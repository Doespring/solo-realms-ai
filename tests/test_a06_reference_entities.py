"""A06/F10 — приложения книги это справочник, а вход в модуль называет книга.

Что здесь закреплено:

* приложение («Приложение А: Монстры») индексируется как справочник —
  монстры, предметы, таблицы, — и кнопки старта у него нет ни при каком
  состоянии разбора (0 приложений со стартом);
* у каждого входа справочника есть страница и цитата, которая на этой
  странице напечатана (100 %);
* `entry_scene` модуля сверяется с его названием: «Эпизод 1: Совет
  Глубоководья» начинается на совете, а не на Башне Ксонтала, которую его
  страницы поминают чаще всего; метка уверенности отличает напечатанное
  название от догадки «первое найденное место»;
* правка типа сущности человеком сохраняется отдельно от текста сцены и
  переживает повторный разбор книги.
"""
import hashlib
import json
import os

import pytest

os.environ.setdefault('SOLO_TEST', '1')

from fastapi.testclient import TestClient

from solo.adventure_library import AUTO_SPLIT_KEY, AdventureLibrary
from solo.app import create_app
from solo.preview_mock import PreviewMock
from solo import scene_edits
from solo.adventure_engine import AdventureService
from solo.book_sections import entries as reference_entries
from solo.prepared_world import normalized, source_span

DOCUMENT = hashlib.sha256(b'a06-tiamat-like-fixture').hexdigest()

#: Заголовки, которые книга печатает в начале своих разделов, и их страницы:
#: колонтитул повторяется на каждой странице раздела, как в настоящей книге.
CHAPTERS = [(19, 23, 'Эпизод 1: Совет Глубоководья'),
            (24, 26, 'Приложение А: Монстры'),
            (27, 29, 'Приложение Б: Магические предметы')]

PROSE = ('Башня Ксонтала стоит над городом, и зал совета ждёт героев в Башне '
         'Ксонтала. Башня Ксонтала охраняется стражей. Городок Врата лежит у её '
         'подножия. ')


def pages():
    book = {number: 'Пролог без названных мест. ' * 4 for number in range(1, 19)}
    book[1] = 'The Rise of Tiamat'
    book[5] = '\n'.join(['Содержание'] + ['Эпизод 1: Совет Глубоководья',
                                          'Приложение А: Монстры',
                                          'Приложение Б: Магические предметы'])
    book[19] = '\n'.join(['Эпизод 1: Совет Глубоководья', PROSE, PROSE,
                          'Монах по имени Леосин Эрлантар приходит на совет.'])
    book[20] = '\n'.join(['Эпизод 1: Совет Глубоководья', PROSE, PROSE,
                          'Каждый персонаж должен пройти проверку на Ловкость (Скрытность) Сл 10.'])
    for number in (21, 22, 23):
        book[number] = '\n'.join(['Эпизод 1: Совет Глубоководья', PROSE, PROSE])
    book[24] = '\n'.join(['Приложение А: Монстры', 'ВЗРОСЛЫЙ СИНИЙ ДРАКОН',
                          'Класс Доспеха 19', 'Хиты 225', 'Скорость 40 футов, полёт 80 футов',
                          'Опасность 16'])
    book[25] = '\n'.join(['Приложение А: Монстры', 'КУЛЬТИСТ',
                          'Класс Доспеха 13', 'Хиты 33', 'Опасность 2'])
    book[26] = '\n'.join(['Приложение А: Монстры', 'Ледяной тролль',
                          'Класс Доспеха 15', 'Хиты 84', 'Опасность 8'])
    book[27] = '\n'.join(['Приложение Б: Магические предметы', 'ДЛИННЫЙ МЕЧ',
                          'Оружие (длинный меч), цена 15 зм'])
    book[28] = '\n'.join(['Приложение Б: Магические предметы', 'ПЛАЩ ЗАЩИТЫ',
                          'Чудесный предмет, редкий'])
    book[29] = '\n'.join(['Приложение Б: Магические предметы', 'ЗЕЛЬЕ ЛЕЧЕНИЯ',
                          'Зелье, обычное'])
    return book


def book(library):
    bundle = library.create('The Rise of Tiamat (образец)', {AUTO_SPLIT_KEY: True})['id']
    text = pages()
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', max(text)))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)',
                   (bundle, DOCUMENT, 'The Rise of Tiamat.pdf', 'book', ''))
        for number, content in text.items():
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (DOCUMENT, number, str(number), content))
        for first, last, heading in CHAPTERS:
            for number in range(first, last + 1):
                db.execute('INSERT INTO candidates(id,document,page,kind,title,body) VALUES(?,?,?,?,?,?)',
                           (hashlib.sha256(('%s:%s' % (number, heading)).encode()).hexdigest()[:32],
                            DOCUMENT, number, 'chapter', heading, '{}'))
    return bundle


def split(tmp_path):
    client = TestClient(create_app(tmp_path / 'solo.sqlite3', PreviewMock(),
                                   experimental=True, offline_mock=True))
    library = client.app.state.adventures
    bundle = book(library)
    library.auto_split(bundle, DOCUMENT)
    return client, library, bundle


def by_title(modules, title):
    return next(row for row in modules if row['title'] == title)


def test_an_appendix_is_a_reference_and_never_offers_a_start(tmp_path):
    _client, _library, bundle = split(tmp_path)
    modules = _library.modules(bundle)
    assert [row['kind'] for row in modules] == ['adventure', 'appendix', 'appendix'], modules
    # Кнопку старта рисует `startable`: у приложения её нет ни при каком
    # состоянии разбора, поэтому «приложений с кнопкой старта» — ноль.
    assert [row['id'] for row in modules if row['kind'] == 'appendix' and row['startable']] == []
    assert [row['id'] for row in modules if row['kind'] == 'adventure' and not row['startable']] == []
    monsters = by_title(modules, 'Приложение А: Монстры')
    assert monsters['reference'] == 'monsters' and monsters['ready'] is False
    assert [row['name'] for row in monsters['scene']['reference']] == [
        'ВЗРОСЛЫЙ СИНИЙ ДРАКОН', 'КУЛЬТИСТ', 'Ледяной тролль'], monsters['scene']['reference']
    items = by_title(modules, 'Приложение Б: Магические предметы')
    assert items['reference'] == 'items'
    assert [row['name'] for row in items['scene']['reference']] == [
        'ДЛИННЫЙ МЕЧ', 'ПЛАЩ ЗАЩИТЫ', 'ЗЕЛЬЕ ЛЕЧЕНИЯ'], items['scene']['reference']
    # Справочник не становится сценой: его входы не попадают в список мест.
    assert monsters['locations'] == 0 and monsters['npcs'] == 0


def test_every_extracted_entity_keeps_the_page_it_is_printed_on(tmp_path):
    _client, library, bundle = split(tmp_path)
    text = pages()
    checked = 0
    for module in library.modules(bundle):
        scene = module['scene']
        rows = [*(scene.get('locations') or []), *(scene.get('npcs') or []),
                *(scene.get('checks') or []), *(scene.get('reference') or [])]
        for row in rows:
            provenance = row['provenance']
            # Страница-источник есть у каждой сущности, и цитата действительно
            # напечатана на этой странице, а не собрана из другой.
            assert provenance['page'] >= 1, (module['id'], row)
            assert provenance['document'] == DOCUMENT
            assert source_span(text[provenance['page']], provenance['quote'])
            checked += 1
    assert checked >= 7, checked


def test_the_entry_scene_is_the_modules_own_name_not_the_first_place(tmp_path):
    _client, library, bundle = split(tmp_path)
    modules = library.modules(bundle)
    episode = by_title(modules, 'Эпизод 1: Совет Глубоководья')
    entry = episode['entry_scene']
    # «Совет Глубоководья» — то, что книга назвала эпизодом. Башня Ксонтала
    # упоминается чаще, поэтому она остаётся местом входа, но метка уверенности
    # говорит, что это догадка: книга назвала эпизод, а не это место.
    assert entry['name'] == 'Совет Глубоководья', entry
    assert entry['place'] == 'Башня Ксонтала' and entry['place_confidence'] == 'check'
    assert entry['confidence'] == 'printed' and entry['binding'] == 'title'
    assert entry['page'] == 19
    assert 'Башня Ксонтала' in [row['name'] for row in episode['scene']['locations']]
    # Мир открывается на сцене, названной книгой, и она единственная известная.
    world = episode['scene']
    assert world['entry'] == entry
    from solo.book_scene import scene_world
    package = scene_world(episode['title'], episode['scene'])[0]
    assert package.locations[0].name == 'Башня Ксонтала'
    known = [place.name for place in package.locations if place.known_to_hero]
    assert known == ['Башня Ксонтала'], known


def test_a_place_the_title_does_not_name_is_marked_as_the_fallback(tmp_path):
    """Модуль без названного места честно говорит, что вход — догадка."""
    from solo.book_scene import entry_scene
    scene = dict(locations=[dict(name='Соляные амбары', page=3, quote='Соляные амбары у воды.',
                                 document=DOCUMENT, provenance=dict(page=3))])
    fallback = entry_scene('', scene['locations'], {3: 'Соляные амбары у воды.'})
    assert fallback['name'] == 'Соляные амбары' and fallback['confidence'] == 'check'
    assert fallback['note']


def test_a_person_corrects_a_type_and_the_correction_survives_a_new_parse(tmp_path):
    client, library, bundle = split(tmp_path)
    modules = library.modules(bundle)
    episode = by_title(modules, 'Эпизод 1: Совет Глубоководья')
    tower = next(row for row in episode['scene']['locations'] if row['name'] == 'Башня Ксонтала')
    assert tower['page'] == 19

    client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
    answer = client.put('/api/adventure/review/library/' + bundle + '/entities',
                        json=dict(module=episode['id'], document=DOCUMENT, page=19,
                                  entity='location', name='Башня Ксонтала', value='monster'))
    assert answer.status_code == 200, answer.text
    corrected = answer.json()
    # Правка меняет тип и область действия, но оставляет цитату и страницу.
    assert 'Башня Ксонтала' not in [row['name'] for row in corrected['scene']['locations']]
    moved = next(row for row in corrected['scene']['reference'] if row['name'] == 'Башня Ксонтала')
    assert (moved['entity_type'], moved['scope'], moved['edited']) == ('monster', 'reference', True)
    assert source_span(pages()[19], moved['provenance']['quote'])
    # Мир, собранный из сцены, больше не начинает и не содержит башню как место.
    from solo.adventure_contracts import AdventureDefinition
    world = AdventureDefinition.model_validate_json(
        library.definition(bundle, episode['id']).model_dump_json()).world
    assert 'Башня Ксонтала' not in [place.name for place in world.locations]

    # Повторный разбор книги: сцена строится заново, правка применяется снова.
    library.auto_split(bundle, DOCUMENT)
    again = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    assert 'Башня Ксонтала' not in [row['name'] for row in again['scene']['locations']]
    kept = next(row for row in again['scene']['reference'] if row['name'] == 'Башня Ксонтала')
    assert (kept['entity_type'], kept['edited']) == ('monster', True)
    assert again['edits'], 'сцена не помнит, что тип исправил человек'


def test_a_correction_points_at_a_printed_row_only(tmp_path):
    client, library, bundle = split(tmp_path)
    episode = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
    answer = client.put('/api/adventure/review/library/' + bundle + '/entities',
                        json=dict(module=episode['id'], document=DOCUMENT, page=20,
                                  entity='location', name='Башня Ксонтала', value='ignore'))
    # Строка напечатана на странице 19, а не 20: правка не угадывает строку.
    assert answer.status_code in (400, 409) and 'нет в сцене' in answer.text


def test_a_monsters_name_is_the_line_above_its_type_line():
    """Книга печатает имя, строку типа и только потом «Класс Доспеха».

    Раньше индекс брал строку прямо над статблоком, и в справочник попадало
    «Средний гуманоид (человек), нейтрально-злой» вместо имени монстра.
    """
    page = ('Приложение А: Монстры\n'
            'Драконий Клык [Dragonfang]\n'
            'Средний гуманоид (человек), нейтрально-злой\n'
            'Класс Доспеха 15\nХиты 33\nОпасность 2')
    found, rejected = reference_entries({91: page}, 'monsters', DOCUMENT)
    assert [row['name'] for row in found] == ['Драконий Клык [Dragonfang]'], found
    assert rejected == []
    # Строка раздела называет вид во множественном числе, сама запись — в
    # единственном: список типов в редакторе нарисован в единственном.
    assert found[0]['kind'] == 'monsters' and found[0]['entity_type'] == 'monster'


def appendix_only(tmp_path):
    """Книга без эпизодов: один бестиарий на две страницы."""
    client = TestClient(create_app(tmp_path / 'solo.sqlite3', PreviewMock(),
                                   experimental=True, offline_mock=True))
    library = client.app.state.adventures
    bundle = library.create('Бестиарий (образец)', {AUTO_SPLIT_KEY: True})['id']
    # Страница с заголовком считается границей раздела только когда на ней есть
    # текст, поэтому у страниц приложения есть проза без имён.
    prose = 'Холодный ветер шёл над водой, и берега не было видно. ' * 4
    text = {1: '\n'.join(['Приложение А: Монстры', prose, 'ВЗРОСЛЫЙ СИНИЙ ДРАКОН',
                           'Класс Доспеха 19', 'Хиты 225', 'Опасность 16']),
            2: '\n'.join(['Приложение А: Монстры', prose, 'КУЛЬТИСТ',
                           'Класс Доспеха 13', 'Хиты 33', 'Опасность 2']),
            3: '\n'.join(['Приложение А: Монстры', prose, 'ЛЕДЯНОЙ ТРОЛЛЬ',
                           'Класс Доспеха 15', 'Хиты 84', 'Опасность 8'])}
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', 3))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)',
                   (bundle, DOCUMENT, 'Бестиарий.pdf', 'book', ''))
        for number, content in text.items():
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (DOCUMENT, number, str(number), content))
            db.execute('INSERT INTO candidates(id,document,page,kind,title,body) VALUES(?,?,?,?,?,?)',
                       (hashlib.sha256(('apx:%s' % number).encode()).hexdigest()[:32],
                        DOCUMENT, number, 'chapter', 'Приложение А: Монстры', '{}'))
    library.auto_split(bundle, DOCUMENT)
    return library, bundle


def test_a_book_of_appendices_only_is_still_a_reference(tmp_path):
    """Бестиарий без эпизодов не становится «эпизодом» с кнопкой старта."""
    library, bundle = appendix_only(tmp_path)
    modules = library.modules(bundle)
    assert [row['kind'] for row in modules] == ['appendix'], modules
    module = modules[0]
    assert module['startable'] is False and module['reference'] == 'monsters'
    assert module['locations'] == 0 and module['npcs'] == 0
    assert [row['name'] for row in module['scene']['reference']] == [
        'ВЗРОСЛЫЙ СИНИЙ ДРАКОН', 'КУЛЬТИСТ', 'ЛЕДЯНОЙ ТРОЛЛЬ'], module['scene']['reference']
    # Старт с приложения не начинается и через службу, а не только на экране.
    with pytest.raises(ValueError):
        AdventureService(None, library).attach('campaign', bundle, 'r1', True, module=module['id'])
    with pytest.raises(ValueError):
        AdventureService(None, library).continue_module('campaign', bundle, module['id'], 'r2', True)


def test_a_check_cannot_become_a_place_and_the_book_still_parses(tmp_path):
    """У проверки нет напечатанного имени: «сделать её местом» сломало бы книгу."""
    client, library, bundle = split(tmp_path)
    episode = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    check = episode['scene']['checks'][0]
    name = scene_edits.check_name(check)
    client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
    refused = client.put('/api/adventure/review/library/' + bundle + '/entities',
                         json=dict(module=episode['id'], document=DOCUMENT, page=check['page'],
                                   entity='check', name=name, value='place'))
    assert refused.status_code in (400, 409), refused.text
    # Отказ не оставил в книге правку, из-за которой не собирается мир:
    # повторный разбор проходит, и настоящая правка после него применяется.
    library.auto_split(bundle, DOCUMENT)
    answer = client.put('/api/adventure/review/library/' + bundle + '/entities',
                        json=dict(module=episode['id'], document=DOCUMENT, page=19,
                                  entity='location', name='Башня Ксонтала', value='monster'))
    assert answer.status_code == 200, answer.text
    # В книге ровно одна правка — настоящая; отказанная не оставила строки.
    assert [row['entity'] for row in library.entity_edits(bundle)] == ['location']


def test_a_correction_uses_the_documents_page_of_the_scene(tmp_path):
    """Источник правки берётся из сцены: чужой `document` в запросе её не теряет."""
    client, library, bundle = split(tmp_path)
    episode = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
    answer = client.put('/api/adventure/review/library/' + bundle + '/entities',
                        json=dict(module=episode['id'], document='0' * 64, page=19,
                                  entity='location', name='Башня Ксонтала', value='monster'))
    assert answer.status_code == 200, answer.text
    assert [row['document'] for row in library.entity_edits(bundle)] == [DOCUMENT]
    corrected = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    assert 'Башня Ксонтала' not in [row['name'] for row in corrected['scene']['locations']]


def test_an_empty_edit_does_not_merge_two_printed_checks():
    """Разные проверки главы не схлопываются, когда правок нет вовсе."""
    scene = dict(locations=[], npcs=[], reference=[],
                 checks=[dict(skill='stealth', ability='dexterity', dc=10, location='Зал совета'),
                         dict(skill='stealth', ability='dexterity', dc=10, location='Двор')])
    kept = scene_edits.apply(scene, [])
    assert len(kept['checks']) == 2, kept['checks']


def test_removing_the_entry_place_moves_the_entry_scene(tmp_path):
    """Правка входа пересчитывает сцену: место входа не остаётся указателем в пустоту."""
    client, library, bundle = split(tmp_path)
    episode = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    assert episode['entry_scene']['place'] == 'Башня Ксонтала'
    client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
    answer = client.put('/api/adventure/review/library/' + bundle + '/entities',
                        json=dict(module=episode['id'], document=DOCUMENT, page=19,
                                  entity='location', name='Башня Ксонтала', value='monster'))
    assert answer.status_code == 200, answer.text
    corrected = answer.json()
    assert corrected['entry_scene']['name'] == 'Совет Глубоководья'
    assert corrected['entry_scene']['place'] == 'Городок Врата', corrected['entry_scene']
    from solo.book_scene import scene_world
    package = scene_world(corrected['title'], corrected['scene'])[0]
    known = [place.name for place in package.locations if place.known_to_hero]
    assert known == ['Городок Врата'], known


def test_a_failed_rebuild_restores_the_previous_correction(tmp_path, monkeypatch):
    """Правка не должна терять работу человека: сбой сборки возвращает прежний тип."""
    client, library, bundle = split(tmp_path)
    episode = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
    first = client.put('/api/adventure/review/library/' + bundle + '/entities',
                       json=dict(module=episode['id'], document=DOCUMENT, page=19,
                                 entity='location', name='Башня Ксонтала', value='item'))
    assert first.status_code == 200, first.text
    assert [row['value'] for row in library.entity_edits(bundle)] == ['item']

    def broken(bundle):
        raise RuntimeError('сборка мира не собралась')
    monkeypatch.setattr(type(library), 'rebuild', broken)
    second = client.put('/api/adventure/review/library/' + bundle + '/entities',
                        json=dict(module=episode['id'], document=DOCUMENT, page=19,
                                  entity='location', name='Башня Ксонтала', value='table'))
    assert second.status_code >= 400
    monkeypatch.undo()
    # Прежняя правка на месте, новой нет, и книга по-прежнему разбирается.
    assert [row['value'] for row in library.entity_edits(bundle)] == ['item']
    again = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    moved = next(row for row in again['scene']['reference'] if row['name'] == 'Башня Ксонтала')
    assert moved['entity_type'] == 'item'


def test_the_entry_scene_stays_inside_the_module_pages(tmp_path):
    """Строка содержания называет эпизод, но вход в него — не оглавление."""
    client, library, bundle = split(tmp_path)
    episode = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
    answer = client.put('/api/adventure/review/library/' + bundle + '/entities',
                        json=dict(module=episode['id'], document=DOCUMENT, page=19,
                                  entity='location', name='Башня Ксонтала', value='monster'))
    assert answer.status_code == 200, answer.text
    entry = answer.json()['entry_scene']
    assert entry['page'] == 19, entry
    assert 19 <= entry['page'] <= 23, entry


def test_an_appendix_stays_an_appendix_when_its_instructions_are_rewritten(tmp_path):
    """Тип раздела — из разбора страниц: перезапись инструкций не оживляет старт."""
    _client, library, bundle = split(tmp_path)
    appendix = next(row for row in library.modules(bundle) if row['kind'] == 'appendix')
    with library.db() as db:
        body = json.loads(db.execute('SELECT body FROM adventure_modules WHERE bundle=? AND id=?',
                                     (bundle, appendix['id'])).fetchone()['body'])
        body['alternate_flags'] = {}
        db.execute('UPDATE adventure_modules SET body=? WHERE bundle=? AND id=?',
                   (json.dumps(body, ensure_ascii=False), bundle, appendix['id']))
    again = next(row for row in library.modules(bundle) if row['id'] == appendix['id'])
    assert again['kind'] == 'appendix' and again['startable'] is False
    with pytest.raises(ValueError):
        AdventureService(None, library).attach('campaign', bundle, 'r3', True, module=appendix['id'])


def test_two_checks_in_different_rooms_are_corrected_one_at_a_time(tmp_path):
    """У проверки есть место: правка одной комнаты не снимает проверку другой."""
    client, library, bundle = split(tmp_path)
    episode = by_title(library.modules(bundle), 'Эпизод 1: Совет Глубоководья')
    scene = episode['scene']
    scene['checks'] = [dict(skill='stealth', ability='dexterity', dc=10, location='Зал совета',
                            page=20, document=DOCUMENT, quote='проверка'),
                       dict(skill='stealth', ability='dexterity', dc=10, location='Двор',
                            page=20, document=DOCUMENT, quote='проверка')]
    with library.db() as db:
        db.execute('UPDATE module_scenes SET body=? WHERE bundle=? AND module=?',
                   (json.dumps(scene, ensure_ascii=False), bundle, episode['id']))
    client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
    name = scene_edits.check_name(scene['checks'][1])
    assert 'Двор' in name
    answer = client.put('/api/adventure/review/library/' + bundle + '/entities',
                        json=dict(module=episode['id'], document=DOCUMENT, page=20,
                                  entity='check', name=name, value='ignore'))
    assert answer.status_code == 200, answer.text
    left = [row.get('location') for row in answer.json()['scene']['checks']]
    assert left == ['Зал совета'], left
