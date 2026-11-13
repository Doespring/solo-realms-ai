"""A05 — один статус книги и требования книги отдельно от её прозы.

Что здесь закреплено:

* `minimum_level`, `recommended_party` и условие продолжения читаются из
  напечатанных страниц и хранят страницу, на которой напечатаны;
* приложение книги — не эпизод: его места попадают в мир эпизода, а старт с
  него не начинается никогда;
* на экране одно число модулей и одно состояние: карточка, экран комплекта и
  закрытая подготовка берут один агрегат одной ревизии;
* «Можно играть» появляется только когда стартовая сцена собрана из книги и
  сверена с её страницей;
* у каждого предупреждения есть действие из закрытого набора;
* сообщение прошлой операции остаётся историей и не становится статусом.
"""
import json
import os

os.environ.setdefault('SOLO_TEST', '1')

from fastapi.testclient import TestClient

from solo.adventure_library import AdventureLibrary
from solo.app import create_app
from solo.book_readiness import ACTIONS, STEPS
from solo.book_scene import (continuation, levels, merge_levels, merge_party, party, requirements,
                             with_front_requirements, with_reference)
from solo.world import ScenarioPackage, WorldLocation, WorldNode

DOCUMENT = 'a' * 64


def library_with_book(tmp_path, *, episodes=11, appendices=1, reviewed=False,
                      scenes=True, start_scene=True, pages=24, pending=0):
    """Книга из эпизодов и приложений, записанная так, как её пишет импорт."""
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Книга с приложением')
    with library.db() as db:
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)', (DOCUMENT, '.pdf', 4096, 'extracted', pages, '{}'))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)', (bundle['id'], DOCUMENT, 'книга.pdf', 'book', ''))
        for number in range(1, pages + 1):
            db.execute('INSERT INTO pages(document,number,content) VALUES(?,?,?)',
                       (DOCUMENT, number, 'Страница ' + str(number) + '. Он стоял у воды и смотрел на огонь. ' * 4))
        for index in range(episodes):
            first = 1 + index
            # `pending` — сколько последних эпизодов ещё ждут проверки: старт
            # готов, а вся книга нет.
            checked = reviewed and index < episodes - pending
            body = dict(id='auto-%02d' % index, title='Эпизод %d' % (index + 1),
                        ruleset='dnd2024', ruleset_version=5,
                        source_ranges=[dict(document=DOCUMENT, first_page=first, last_page=first + 1)],
                        reviewed=checked,
                        unsupported=[] if checked else ['Игровые инструкции модуля ещё не проверены'],
                        minimum_level=8, recommended_party=4)
            db.execute('INSERT INTO adventure_modules VALUES(?,?,?,1)',
                       (bundle['id'], body['id'], json.dumps(body, ensure_ascii=False)))
            if scenes:
                scene = scene_for(first, with_places=start_scene or index)
                db.execute('INSERT INTO module_scenes VALUES(?,?,?,1)',
                           (bundle['id'], body['id'], json.dumps(scene, ensure_ascii=False)))
        for index in range(appendices):
            first = 1 + episodes + index
            body = dict(id='auto-apx-%02d' % index, title='Приложение %s' % 'АБВГД'[index],
                        ruleset='dnd2024', ruleset_version=5,
                        source_ranges=[dict(document=DOCUMENT, first_page=first, last_page=first + 1)],
                        reviewed=True, unsupported=['Приложение книги: справочные страницы, а не сценарий'],
                        alternate_flags={'kind': 'appendix'})
            db.execute('INSERT INTO adventure_modules VALUES(?,?,?,1)',
                       (bundle['id'], body['id'], json.dumps(body, ensure_ascii=False)))
            if scenes:
                db.execute('INSERT INTO module_scenes VALUES(?,?,?,1)',
                           (bundle['id'], body['id'],
                            json.dumps(scene_for(first, with_places=True), ensure_ascii=False)))
    return library, bundle['id']


def scene_for(page, *, with_places=True):
    return dict(document=DOCUMENT, pages=[page, page + 1],
                locations=[dict(name='Соляные амбары', quote='Соляные амбары стояли у воды.', page=page)] if with_places else [],
                npcs=[], checks=[], rejected=[],
                levels=dict(minimum=8, maximum=8, quote='', minimum_quote='', maximum_quote='',
                            minimum_page=page, maximum_page=page),
                party=dict(size=4, maximum=4, quote='', page=page), continuation=None,
                counts=dict(locations=1 if with_places else 0, npcs=0, checks=0))


def test_the_book_prints_its_level_party_and_continuation_with_pages():
    page = ('Это приключение рассчитано на четырёх персонажей 8-го уровня, и его герои '
            'начинают в Соляных амбарах. Оно является прямым продолжением приключения «Логово».')
    found = levels(page)
    # Одно число книга печатает как «рассчитано на 8-й уровень»; диапазон она
    # печатает двумя числами. Максимум сам по себе не выдумывается.
    assert found['minimum'] == 8, found
    assert (levels('Для группы 5–8 уровней подойдут любые герои.')['minimum'],
            levels('Для группы 5–8 уровней подойдут любые герои.')['maximum']) == (5, 8)
    size = party(page)
    assert (size['size'], size['maximum']) == (4, 4), size
    after = continuation(page)
    assert 'прямым продолжением' in after['text'], after
    merged = merge_levels([dict(found, page=6)])
    rows = requirements(dict(document=DOCUMENT, levels=merged, party=merge_party([dict(size, page=6)]),
                             continuation=dict(after, page=6)), 'auto-01')
    kinds = {row['kind']: row for row in rows}
    # Каждый факт несёт страницу, на которой напечатан: экран может открыть её.
    assert kinds['minimum_level']['value'] == 8 and kinds['minimum_level']['page'] == 6
    assert kinds['recommended_party']['value'] == 4 and kinds['recommended_party']['page'] == 6
    assert kinds['continuation']['page'] == 6
    # Требования не смешиваются с художественным текстом: в сцене их нет.
    assert 'locations' not in rows[0] and 'minimum_level' not in json.dumps(scene_for(6))


def test_a_fiction_number_is_not_a_requirement_but_the_title_page_is(tmp_path):
    """Требование — предложение о приключении, а не любая строка с числом.

    Настоящая книга печатает рядом и «уничтожить несколько групп 15 уровня», и
    «эффект заклинания на группу из 9 персонажей», и «поделите экипаж на группы
    по 5 человек». Ни одно из них не условие игры. А на титульной странице перед
    первой главой — «для персонажей, начиная с 8 уровня» и «идеальный размер
    группы — 4 персонажа»: это условие, и его страница сохраняется.
    """
    fiction = ('Если она выйдет из портала в полных силах, она легко сможет уничтожить '
               'несколько групп 15 уровня.')
    spell = ('Если эффект распространяется на 5 футов вокруг цели заклинания, то группа из '
             '9 персонажей может чувствовать себя в полной безопасности.')
    crew = 'Чтобы ускорить процесс, поделите экипаж на группы по 5 человек и сделайте спасбросок.'
    battle = ('Если все штрафы применены, показатель опасности Тиамат снижается до 18 — что всё '
              'ещё смертельная угроза для персонажей 15го уровня.')
    for sentence in (fiction, spell, crew, battle):
        found = levels(sentence)
        assert found['minimum'] is None and found['maximum'] is None, (sentence, found)
        assert party(sentence) is None, (sentence, party(sentence))

    introduction = ('Тирания драконов: Пробуждение Тиамат — это сценарий для ДнД продолжение '
                    '1 тома Клад Королевы Драконов для персонажей, начиная с 8 уровня. К концу '
                    'сценария персонажи должны достигнуть 15 уровня (или быть близки к нему). '
                    'Идеальный размер группы — 4 персонажа.')
    library, bundle = library_with_book(tmp_path, episodes=1, appendices=0, reviewed=True, pages=24)
    with library.db() as db:
        db.execute('UPDATE pages SET content=? WHERE document=? AND number=6', (introduction, DOCUMENT))
    chapter = scene_for(2, with_places=True)
    chapter['levels'] = dict(minimum=None, maximum=None, quote='', minimum_quote='', maximum_quote='',
                             minimum_page=None, maximum_page=None)
    chapter['party'] = None
    merged = with_front_requirements(library, DOCUMENT, 1, 6, chapter)
    rows = {row['kind']: row for row in requirements(merged, 'auto-00')}
    # Условие книги пришло с титульной страницы, и страница названа.
    assert (rows['minimum_level']['value'], rows['minimum_level']['page']) == (8, 6), rows
    assert (rows['recommended_party']['value'], rows['recommended_party']['page']) == (4, 6), rows
    # Но места, люди и проверки по-прежнему только из главы: выходные данные
    # введения не стали локацией.
    assert merged['counts']['locations'] == chapter['counts']['locations'] == 1, merged['counts']


def test_the_appendix_is_a_place_of_the_book_but_never_a_start(tmp_path):
    library, bundle = library_with_book(tmp_path, episodes=2, appendices=1, reviewed=True)
    modules = library.modules(bundle)
    kinds = {row['id']: row['kind'] for row in modules}
    assert kinds['auto-apx-00'] == 'appendix' and kinds['auto-00'] == 'adventure'
    appendix = next(row for row in modules if row['kind'] == 'appendix')
    assert appendix['locations'] == 1, appendix
    # Старт выбирается только среди эпизодов — приложение в список не попадает.
    from solo.creation_handoff import book_of
    book = book_of(library, dict(adventure_bundle=bundle, adventure_module='auto-apx-00'))
    assert book['module'] == 'auto-00' and book['ready'], book
    from solo.preparation import Preparation
    from solo.storage import Store
    store = Store(tmp_path / 'solo.sqlite3')
    preparation = Preparation(tmp_path, store)
    assert preparation is not None
    status = library.readiness(bundle)
    assert status['counts']['appendices'] == 1 and status['counts']['episodes'] == 2
    assert status['start']['id'] == 'auto-00'


def test_the_card_and_the_review_screen_print_one_number_from_one_revision(tmp_path):
    library, bundle = library_with_book(tmp_path, episodes=11, appendices=1)
    with library.db() as db:
        db.execute('INSERT INTO jobs(id,bundle,document,original_name,purpose,status,created,done,total,message) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?)',
                   ('job-1', bundle, DOCUMENT, 'книга.pdf', 'book', 'complete', 0.0, 24, 24,
                    'Извлечено страниц: 24. Модулей: 12. Игровые инструкции по-прежнему требуют проверки.'))
    card = next(row for row in library.list() if row['id'] == bundle)
    review = library.review(bundle)
    readiness = review['readiness']
    # Одно число модулей и одна ревизия — на карточке, на экране подготовки и в
    # таблице, откуда рисуется список. Пара 12/11 невозможна по построению.
    assert card['modules'] == readiness['counts']['modules'] == len(review['modules'])
    assert card['readiness']['counts'] == readiness['counts']
    assert card['readiness']['revision'] == readiness['revision'] == card['revision']
    assert readiness['counts']['episodes'] == 11 and readiness['counts']['appendices'] == 1
    # «Инструкции проверены» не появляется над списком непроверенных модулей.
    assert card['status'] != 'instructions_reviewed', card['status']
    assert 'проверены' not in card['readiness']['headline'].lower() or 'не' in card['readiness']['headline'], \
        card['readiness']['headline']
    # Сообщение прошлой операции осталось историей с числом 12 и не попало в статус.
    assert 'Модулей: 12' in card['jobs'][0]['message']
    assert '12' not in card['readiness']['headline'], card['readiness']['headline']


def test_the_status_says_instructions_are_checked_only_when_every_episode_is(tmp_path):
    library, bundle = library_with_book(tmp_path, episodes=3, appendices=0, reviewed=False)
    assert library.readiness(bundle)['status'] == 'requires_review'
    library, bundle = library_with_book(tmp_path / 'checked', episodes=3, appendices=0, reviewed=True)
    status = library.readiness(bundle)
    assert status['instructions_checked'] is False  # строгие инструкции — отдельный факт
    assert 'проверены' not in status['headline'].lower() or 'не' in status['headline'].lower()


def test_can_play_only_when_the_start_scene_is_read_from_the_book(tmp_path):
    library, bundle = library_with_book(tmp_path, episodes=2, appendices=0, reviewed=True, start_scene=False)
    status = library.readiness(bundle)
    assert status['start_ready'] is False, status['headline']
    assert status['steps'][-1]['state'] == 'current' or status['steps'][-1]['state'] == 'pending'
    assert 'Играть пока нельзя' in status['headline'], status['headline']
    library, bundle = library_with_book(tmp_path / 'valid', episodes=2, appendices=0, reviewed=True, pending=1)
    status = library.readiness(bundle)
    assert status['start_ready'] is True, status['start_problem']
    assert 'Можно играть' in status['headline'], status['headline']
    assert status['steps'][-1]['state'] == 'done'
    # «Готова вся книга» — отдельный ответ: старт открыт, второй эпизод ждёт.
    assert status['book_ready'] is False, status['counts']
    assert 'Вся книга ещё не готова' in status['headline'], status['headline']


def test_every_warning_carries_an_action(tmp_path):
    states = []
    library, bundle = library_with_book(tmp_path / 'empty', episodes=0, appendices=0)
    states.append(library.readiness(bundle))
    library, bundle = library_with_book(tmp_path / 'pending', episodes=11, appendices=1)
    states.append(library.readiness(bundle))
    library, bundle = library_with_book(tmp_path / 'valid', episodes=2, appendices=0, reviewed=True)
    states.append(library.readiness(bundle))
    library, bundle = library_with_book(tmp_path / 'noplace', episodes=2, appendices=0, reviewed=True,
                                        start_scene=False)
    states.append(library.readiness(bundle))
    seen = set()
    for status in states:
        for warning in status['warnings']:
            assert warning['action'] in ACTIONS, warning
            assert warning['text'].strip(), warning
            seen.add(warning['code'])
    assert {'start_invalid', 'instructions_missing'} <= seen, seen


def test_the_six_steps_are_named_once_and_in_order():
    library = None
    assert [row[0] for row in STEPS] == ['upload', 'extract', 'index', 'prepare', 'validate', 'playable']


def test_the_api_gives_the_card_and_the_review_the_same_aggregate(tmp_path):
    library, bundle = library_with_book(tmp_path, episodes=4, appendices=1)
    with TestClient(create_app(tmp_path / 'solo.sqlite3')) as client:
        card = next(row for row in client.get('/api/adventure/library').json() if row['id'] == bundle)
        assert card['readiness']['counts']['modules'] == 5
        client.post('/api/adventure/review/session', json={'confirm_spoilers': True})
        review = client.get('/api/adventure/review/library/' + bundle).json()
        assert review['readiness']['counts'] == card['readiness']['counts']
        assert review['readiness']['revision'] == card['readiness']['revision']


def test_the_import_progress_names_episodes_and_appendices_not_one_module_number(tmp_path):
    library, bundle = library_with_book(tmp_path, episodes=11, appendices=1)
    with library.db() as db:
        db.execute('INSERT INTO jobs(id,bundle,document,original_name,purpose,status,created,done,total,message) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?)',
                   ('job-1', bundle, DOCUMENT, 'книга.pdf', 'book', 'complete', 0.0, 24, 24,
                    'Извлечено страниц: 24. Модулей: 12.'))
    counts = library.counts(bundle, DOCUMENT)
    # Экран мастера после импорта называет те же два числа, что и состояние
    # книги: «12 модулей» было суммой эпизодов и приложения.
    assert (counts['modules'], counts['episodes'], counts['appendices']) == (12, 11, 1), counts
    assert counts['episodes'] + counts['appendices'] == counts['modules']
    with TestClient(create_app(tmp_path / 'solo.sqlite3')) as client:
        progress = client.get('/api/adventure/jobs/job-1').json()
        assert (progress['episodes'], progress['appendices'], progress['modules']) == (11, 1, 12), progress
        readiness = client.get('/api/adventure/library').json()
        card = next(row for row in readiness if row['id'] == bundle)['readiness']['counts']
        assert (card['episodes'], card['appendices']) == (progress['episodes'], progress['appendices'])


def header_pdf(lines):
    """Настоящий PDF, у которого на каждой странице напечатана одна строка."""
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter()
    for line in lines:
        page = writer.add_blank_page(612, 792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                 NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject(
            {NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 12 Tf 30 750 Td (' + line + ') Tj ET').encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_auto_split_calls_an_appendix_an_appendix_and_never_a_start(tmp_path):
    from solo.adventure_worker import run
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Сборник с приложением', {'auto_split': True})
    # Английские заголовки: тот же распознаватель, но строка PDF пишется в
    # ASCII-кодировке шрифта Helvetica.
    lines = ['Chapter %d. Shore' % n for n in (1, 1, 1, 2, 2, 2)]
    lines += ['APPENDIX A'] * 3
    path = tmp_path / 'book.pdf'
    path.write_bytes(header_pdf(lines))
    job = library.enqueue(bundle['id'], path, path.name, 'book')
    run(library.data, job['id'])
    modules = library.modules(bundle['id'])
    kinds = {row['id']: row['kind'] for row in modules}
    assert sorted(kinds.values()) == ['adventure', 'adventure', 'appendix'], kinds
    appendix = next(row for row in modules if row['kind'] == 'appendix')
    assert appendix['ready'] is False, appendix
    status = library.readiness(bundle['id'])
    assert status['counts']['episodes'] == 2 and status['counts']['appendices'] == 1
    assert status['start'] and status['start']['id'] != appendix['id']


def test_appendix_places_enter_the_episode_world_without_becoming_its_start():
    episode = ScenarioPackage(name='Эпизод', source='book-scene-v1',
                              locations=[WorldLocation(name='Соляные амбары')],
                              nodes=[WorldNode(id='scene-1', name='Соляные амбары', coordinates=[2, 2])])
    appendix = ScenarioPackage(name='Приложение', source='book-scene-v1',
                               locations=[WorldLocation(name='Северный маяк', known_to_hero=True),
                                          WorldLocation(name='Соляные амбары')],
                               nodes=[WorldNode(id='scene-1', name='Северный маяк', coordinates=[2, 2]),
                                      WorldNode(id='scene-2', name='Соляные амбары', coordinates=[4, 2])])
    merged = with_reference(episode, [appendix])
    places = {place.name: place.known_to_hero for place in merged.locations}
    # Место приложения пришло в мир эпизода и осталось неоткрытым.
    assert places == {'Соляные амбары': False, 'Северный маяк': False}, places
    nodes = {node.name: node.id for node in merged.nodes}
    # У места приложения свой узел: узел эпизода не перезаписан.
    assert nodes['Соляные амбары'] == 'scene-1' and nodes['Северный маяк'].startswith('apx-'), nodes
    # Повторное слияние ничего не удваивает.
    again = with_reference(merged, [appendix])
    assert len(again.locations) == 2 and len(again.nodes) == 2
