"""A chapter cut from a book carries the scene its pages print.

The book a player imports is cut into page ranges; a range alone is not a scene.
These tests pin what a range must carry before the game may open on it: at least
one place, at least one person and at least one check with its DC — each with the
page and the sentence it was read from — plus the level span the chapter states,
the chapter's own title rather than the credits line, and the refusal to store a
person whose name the cited sentence never printed.

The pages are the book's own («Клад Королевы Драконов», RU); nothing is invented
in the fixtures, so every assertion below is also a check of the extractor
against real typography — running headers, soft line breaks, translator glosses.
"""
import hashlib

import pytest
from fastapi.testclient import TestClient

from solo.adventure_library import AUTO_SPLIT_KEY, AdventureLibrary, chapter_starts
from solo.app import create_app
from solo.book_scene import checks, grounded, module_scene, scene_world
from solo.prepared_world import source_span
from solo.preview_mock import PreviewMock
from fixtures.hoard_pages import PAGES

DOCUMENT = hashlib.sha256(b'klad-korolevy-drakonov-fixture').hexdigest()

#: Which chapter every printed header belongs to, as the importer stores it.
CHAPTERS = [(8, 'Эпизод 1: Гринест в огне'), (9, 'Эпизод 1: Гринест в огне'),
            (16, 'Эпизод 2: Лагерь налетчиков'), (19, 'Эпизод 2: Лагерь налетчиков'),
            (22, 'Эпизод 2: Лагерь налетчиков')]


def app(tmp_path):
    return TestClient(create_app(tmp_path / 'solo.sqlite3', PreviewMock(), experimental=True, offline_mock=True))


def book(library, pages=PAGES, chapters=CHAPTERS, title='Клад Королевы Драконов'):
    """A bundle holding one extracted document, exactly as the importer leaves it."""
    bundle = library.create(title, {AUTO_SPLIT_KEY: True})['id']
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', max(pages)))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)', (bundle, DOCUMENT, 'book.pdf', 'book', ''))
        for number, text in pages.items():
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (DOCUMENT, number, str(number), text))
        for number, heading in chapters:
            db.execute('INSERT INTO candidates(id,document,page,kind,title,body) VALUES(?,?,?,?,?,?)',
                       (hashlib.sha256(heading.encode() + bytes([number % 256])).hexdigest()[:32],
                        DOCUMENT, number, 'chapter', heading, '{}'))
    return bundle


def test_a_chapter_becomes_a_scene_with_places_people_and_checks(tmp_path):
    client = app(tmp_path)
    library = client.app.state.adventures
    bundle = book(library)
    library.auto_split(bundle, DOCUMENT)

    modules = client.get('/api/adventure/library/' + bundle + '/modules').json()
    assert len(modules) == 2, modules
    first = modules[0]
    assert first['ready'] is True
    assert first['title'] == 'Эпизод 1: Гринест в огне'
    # The seven pages the book prints in front of its first chapter (cover,
    # credits, contents) come with the module as material instead of becoming a
    # module of their own, and the module still starts where the chapter starts.
    assert first['source_pages'] == [[8, 15], [1, 7]]
    assert (first['locations'], first['npcs'], first['checks']) == (1, 1, 1)
    scene = first['scene']
    place = scene['locations'][0]
    assert place['name'] == 'Гринест' and place['kind'] == 'town'
    assert (place['page'], place['document']) == (8, DOCUMENT)
    assert scene['npcs'][0]['name'] == 'Леосин Эрлантар'
    check = scene['checks'][0]
    assert (check['skill'], check['dc'], check['page']) == ('stealth', 10, 9)


def test_every_scene_entry_can_be_checked_against_its_own_page(tmp_path):
    client = app(tmp_path)
    library = client.app.state.adventures
    bundle = book(library)
    library.auto_split(bundle, DOCUMENT)
    scene = library.modules(bundle)[0]['scene']
    # The prober's acceptance, run as a test: open each quote and compare it with
    # the page index. A quote that is not on its page fails here, not in review.
    for entry in [*scene['locations'], *scene['npcs'], *scene['checks']]:
        page = library.page_text(bundle, entry['document'], entry['page'])
        assert page, entry
        assert source_span(page, entry['quote']), entry


def test_the_stored_world_carries_the_printed_check_with_its_page(tmp_path):
    client = app(tmp_path)
    library = client.app.state.adventures
    bundle = book(library)
    library.auto_split(bundle, DOCUMENT)
    definition = library.definition(bundle, library.modules(bundle)[0]['id'])
    place = definition.world.locations[0]
    assert place.name == 'Гринест'
    assert [npc.name for npc in place.npc] == ['Леосин Эрлантар']
    key, check = next(iter(place.checks.items()))
    assert key == 'loc:l1:skill:stealth:10'
    assert (check.origin, check.skill, check.dc) == ('source', 'stealth', 10)
    assert check.source.page == 9 and 'Скрытность' in check.source.quote


def test_the_credits_line_does_not_become_a_chapter_title():
    rows = [(2, 'chapter', 'Эпизод 5 - LeoTrickster, stivie'), (2, 'chapter', 'Эпизод 8 - korfax'),
            (49, 'chapter', 'Эпизод 5: Придорожное гостеприимство'),
            (50, 'chapter', 'Эпизод 5: Придорожное гостеприимство'),
            (51, 'chapter', 'Эпизод 5: Придорожное гостеприимство')]
    starts = dict(chapter_starts(rows))
    # The credits page names the episode once; the episode itself names it three
    # times. The run decides, and the title must be the book's own header.
    assert starts[49] == 'Эпизод 5: Придорожное гостеприимство'


def test_the_level_span_is_the_printed_one_and_a_tower_floor_is_not(tmp_path):
    client = app(tmp_path)
    library = client.app.state.adventures
    bundle = book(library)
    library.auto_split(bundle, DOCUMENT)
    second = client.get('/api/adventure/library/' + bundle + '/modules').json()[1]
    assert second['maximum_level'] == 3
    # «Некоторые хижины на уровне 2» is a floor of the camp, not a level a party
    # should be, so it must not become the minimum.
    assert second['minimum_level'] is None
    # The number keeps the sentence that printed it: a span merged out of several
    # pages cannot be checked otherwise, and the last page of a book states the
    # levels of the whole adventure, not of the chapter it happens to end.
    levels = second['scene']['levels']
    assert levels['maximum_quote'] and levels['maximum_quote'] in PAGES[22]
    assert levels['minimum_quote'] == ''


def test_a_person_the_page_never_names_is_rejected_not_stored(tmp_path):
    library = AdventureLibrary(tmp_path)
    pages = {8: PAGES[8]}
    quote = PAGES[8].split('\n')[1]
    invented = dict(kind='npc', name='Ксилот Разрушитель', page=8, quote=quote, location='')
    accepted, rejected = grounded(pages, [invented])
    assert accepted == [] and rejected and 'не встречается' in rejected[0]['reason']
    # The same sentence does prove a person the book really printed there.
    real = dict(kind='npc', name='Дхарва Скаттерхарт', page=8, quote=quote, location='')
    accepted, rejected = grounded(pages, [real])
    assert [item['name'] for item in accepted] == ['Дхарва Скаттерхарт'] and rejected == []


def test_a_quote_that_is_not_on_its_page_is_rejected(tmp_path):
    library = AdventureLibrary(tmp_path)
    stale = dict(kind='location', name='Гринест', page=9, quote='Городок Гринест был основан халфлингом.')
    accepted, rejected = grounded({9: PAGES[9]}, [stale])
    assert accepted == [] and rejected[0]['reason'] == 'Цитата не найдена на странице'


def test_a_chapter_without_a_printed_place_gets_no_invented_world(tmp_path):
    library = AdventureLibrary(tmp_path)
    bundle = book(library, pages={2: PAGES[2], 51: PAGES[51]}, chapters=[(51, 'Эпизод 8: Замок в облаках')])
    scene = module_scene(library, DOCUMENT, 51, 51, title='Эпизод 8: Замок в облаках')
    world, _checks = scene_world('Эпизод 8: Замок в облаках', scene)
    assert world is None and scene['locations'] == []


def test_module_scene_keeps_the_chapter_title_place_and_its_page(tmp_path):
    library = AdventureLibrary(tmp_path)
    book(library, pages={8: PAGES[8], 9: PAGES[9], 16: PAGES[16], 19: PAGES[19], 22: PAGES[22]})
    scene = module_scene(library, DOCUMENT, 8, 15, title='Эпизод 1: Гринест в огне')
    assert scene['locations'][0]['quote'].startswith('Городок Гринест')
    assert scene['locations'][0]['page'] == 8
    assert all(entry['binding'] in ('sentence', 'chapter', 'printed') for entry in [*scene['locations'], *scene['npcs'], *scene['checks']])
    assert scene['counts'] == dict(locations=1, npcs=1, checks=1)


def test_the_scene_is_playable_in_the_campaign_it_opens(tmp_path):
    """The place, the person and the check reach the game, not only the report."""
    import uuid

    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.auto_split(bundle, DOCUMENT)
        draft = client.post('/api/preparation/drafts', json={}).json()
        body = draft['body']
        body.update(adventure_bundle=bundle, ruleset='dnd2014')
        saved = client.put('/api/preparation/drafts/' + draft['id'], json={'body': body, 'revision': draft['revision']})
        assert saved.status_code == 200, saved.text
        world = client.post('/api/preparation/drafts/' + draft['id'] + '/world', json={}).json()
        assert world['bound'] is True, world
        created = client.post('/api/prototype/campaigns', json=dict(
            name='Лира', request_id='r150-' + uuid.uuid4().hex[:8], build=client.get('/api/prototype/options').json()['default_build'],
            adventure='sandbox', preparation_draft=draft['id'], encounter_monsters=[]))
        assert created.status_code == 200, created.text
        state = client.app.state.store.campaign(created.json()['id'])['state']
        place = next(place for place in state['locations'] if place['name'] == 'Гринест')
        assert [npc['name'] for npc in place['npc']] == ['Леосин Эрлантар']
        # The check is stored where the engine reads it, with the page that
        # printed the DC, so the player can ask for it and the engine rolls it.
        key, check = next(iter(place['checks'].items()))
        assert key == 'loc:l1:skill:stealth:10'
        assert (check['dc'], check['skill'], check['source']['page']) == (10, 'stealth', 9)


def test_the_scene_is_read_twice_the_same_way(tmp_path):
    library = AdventureLibrary(tmp_path)
    book(library, pages={8: PAGES[8], 9: PAGES[9], 22: PAGES[22]})
    first = module_scene(library, DOCUMENT, 8, 9, title='Эпизод 1: Гринест в огне')
    second = module_scene(library, DOCUMENT, 8, 9, title='Эпизод 1: Гринест в огне')
    assert first == second

def test_a_dc_printed_as_a_formula_is_not_a_number_to_roll_against():
    """«Сл 5 плюс урон» is a formula: five is not the DC, and storing it would
    turn a save the book made conditional into a certain miss."""
    found = checks(PAGES[63])
    assert [item for item in found if item['dc'] == 5] == []
    # The printed sentence is still the book's; only the invented number is gone.
    assert 'Сл 5 плюс урон' in PAGES[63]


def test_a_person_named_on_two_pages_is_one_person(tmp_path):
    """The world refuses two NPCs with one name, so a chapter that introduces the
    same person twice must not hand it two entries and fail the campaign start."""
    library = AdventureLibrary(tmp_path)
    # The same printed page twice in one chapter: «Монах по имени Леосин
    # Эрлантар» is read on both, and it is one monk.
    book(library, pages={8: PAGES[8], 9: PAGES[8]}, chapters=[(8, 'Эпизод 1: Гринест в огне')])
    scene = module_scene(library, DOCUMENT, 8, 9, title='Эпизод 1: Гринест в огне')
    assert [npc['name'] for npc in scene['npcs']] == ['Леосин Эрлантар']
    assert scene['npcs'][0]['mentions'] == 2


def test_a_chapter_that_names_no_place_is_anchored_to_its_printed_title(tmp_path):
    """A page with no place noun still prints the chapter title, and the checks it
    carries need a place to belong to. The anchor says it came from the headline,
    and the episode number is not part of the name."""
    library = AdventureLibrary(tmp_path)
    book(library, pages={51: PAGES[51]}, chapters=[(51, 'Эпизод 5: Придорожное гостеприимство')])
    scene = module_scene(library, DOCUMENT, 51, 51, title='Эпизод 5: Придорожное гостеприимство')
    place = scene['locations'][0]
    assert (place['name'], place['kind'], place['binding']) == ('Придорожное гостеприимство', 'chapter', 'title')
    # The quote is the printed headline itself, a line of the page.
    assert (place['page'], place['quote']) == (51, 'Эпизод 5: Придорожное гостеприимство')
    assert place['quote'] in PAGES[51]


def test_a_credits_line_two_pages_before_the_chapter_does_not_name_it():
    """The credits page and the chapter can fall into one run: a tie is broken by
    the last page of the run and by a header reading, not by length."""
    rows = [(2, 'chapter', 'Эпизод 5 - LeoTrickster, stivie'),
            (4, 'chapter', 'Эпизод 5: Придорожное гостеприимство')]
    starts = dict(chapter_starts(rows))
    assert 'Эпизод 5: Придорожное гостеприимство' in starts.values()

def test_a_credits_line_on_the_last_page_of_a_run_does_not_name_the_chapter():
    """A tie is broken by the printed form, not by page order: a credits line
    printed on the last page of the run must not become the chapter title."""
    rows = [(20, 'chapter', 'Эпизод 3: Драконья кладка'),
            (22, 'chapter', 'Эпизод 3 - LeoTrickster, stivie')]
    assert dict(chapter_starts(rows))[20] == 'Эпизод 3: Драконья кладка'
