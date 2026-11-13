"""The checks a book printed reach the campaign, the client and the die.

r149 carried the place, the person and the page into a campaign made from a
book; r150 read the check out of the page; r152 pins the last link of that
chain. The check is in the campaign the player opens, a client can see it, and
asking for it by the name the book uses ("Проверить: Скрытность") rolls against
the book's own DC instead of being refused as an unregistered check.

The pages are the book's own («Клад Королевы Драконов», RU) through
``fixtures.hoard_pages``, so the DC under test is the printed one.
"""
import hashlib
import re
import uuid

from fastapi.testclient import TestClient

from solo.adventure_library import AUTO_SPLIT_KEY, AdventureLibrary
from solo.app import create_app
from solo.preview_mock import PreviewMock
from solo.source_checks import match
from solo.world import WorldLocation
from fixtures.hoard_pages import PAGES

DOCUMENT = hashlib.sha256(b'klad-korolevy-drakonov-fixture').hexdigest()
CHAPTERS = [(8, 'Эпизод 1: Гринест в огне'), (9, 'Эпизод 1: Гринест в огне'),
            (16, 'Эпизод 2: Лагерь налетчиков'), (19, 'Эпизод 2: Лагерь налетчиков'),
            (22, 'Эпизод 2: Лагерь налетчиков')]
CHECK_ID = 'loc:l1:skill:stealth:10'
DC_PAGE = 9


def app(tmp_path):
    return TestClient(create_app(tmp_path / 'solo.sqlite3', PreviewMock(), experimental=True, offline_mock=True))


def book(library):
    """A bundle holding one extracted document, exactly as the importer leaves it."""
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
    """The dialog path: a draft that knows its bundle, then the published world."""
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
    """The hero editor starts a campaign on that draft and the book is bound to it."""
    created = client.post('/api/prototype/campaigns', json=dict(
        name='Лира', request_id='r152-' + uuid.uuid4().hex[:8],
        build=client.get('/api/prototype/options').json()['default_build'],
        adventure='sandbox', preparation_draft=draft_id, encounter_monsters=[]))
    assert created.status_code == 200, created.text
    return created.json()['id']


def test_the_checks_the_pages_printed_are_in_the_campaign_the_player_opens(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.auto_split(bundle, DOCUMENT)
        module = client.get('/api/adventure/library/' + bundle + '/modules').json()[0]
        draft_id, _world = prepared(client, bundle)
        campaign = load(client, draft_id)

        state = client.app.state.store.campaign(campaign)['state']
        place = state['locations'][0]
        assert place['name'] == 'Гринест'
        # One entry for every check the module says it read out of the pages: the
        # scene report and the stored place may not disagree.
        assert len(place['checks']) == len(module['scene']['checks']) == 1
        assert list(place['checks']) == [CHECK_ID]
        check = place['checks'][CHECK_ID]
        # The DC keeps the page and the sentence that printed it.
        assert (check['dc'], check['skill'], check['origin']) == (10, 'stealth', 'source')
        assert check['source']['page'] == DC_PAGE and 'Скрытность' in check['source']['quote']
        # The stored key is a check id the world itself accepts, not a loose string.
        assert re.match(r'^loc:[a-z0-9][a-z0-9_-]{0,59}:(?:skill|ability|saving_throw):[a-z][a-z0-9_]{0,39}:\d{1,3}$',
                        CHECK_ID)
        WorldLocation.model_validate(place)

        # A client reads the same check. The projection used to drop the field, so
        # the browser saw a place with no check to offer while the engine had it.
        public = client.get('/api/campaigns/' + campaign).json()['state']['locations'][0]
        assert list(public['checks']) == list(place['checks'])
        shown = public['checks'][CHECK_ID]
        assert (shown['dc'], shown['skill'], shown['source']['page']) == (10, 'stealth', DC_PAGE)
        # The numbers travel; the sentence that printed them stays GM text, the
        # same rule that keeps sealed_text out of the browser.
        assert 'quote' not in shown['source'] and 'trigger' not in shown


def test_asking_for_the_check_by_its_printed_name_rolls_the_books_dc(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.auto_split(bundle, DOCUMENT)
        draft_id, _world = prepared(client, bundle)
        campaign = load(client, draft_id)
        before = client.app.state.store.campaign(campaign)
        version, turns = before['version'], before['state']['turn']

        turn = client.post('/api/campaigns/' + campaign + '/command-turn', json=dict(
            request_id='r152-turn-' + uuid.uuid4().hex[:8], text='Проверить: Скрытность',
            expected_version=version))
        assert turn.status_code == 200, turn.text
        answer = turn.json()
        # A refusal here is the defect this test exists for: the engine used to
        # know only its own skill registry (no DC) and rejected the book's key.
        assert answer['status'] == 'resolved', answer
        assert answer['command']['action'] == {'kind': 'check', 'check_id': CHECK_ID}
        text = answer['narrative']
        assert 'Скрытность' in text and 'против Сл 10' in text, text
        # The die and the modifier are the engine's, not the narrator's prose.
        assert re.search(r'Скрытность: \d+ против Сл 10 — (?:успех|неудача)', text), text
        stored = client.app.state.store.event_for_action(answer['events'][0]['action'])
        assert stored['results'][0]['text'] == text
        state = client.app.state.store.campaign(campaign)['state']
        assert state['turn'] == turns + 1 and client.app.state.store.campaign(campaign)['version'] == version + 1


def test_the_check_is_offered_by_its_printed_name_and_dc(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.auto_split(bundle, DOCUMENT)
        draft_id, _world = prepared(client, bundle)
        campaign = load(client, draft_id)
        screen = client.get('/api/campaigns/' + campaign + '/screen').json()
        labels = [command['label'] for command in screen['commands']]
        assert 'Проверить: Скрытность (Сл 10, книга: стр. 9)' in labels, labels
        assert [command['parameters']['check_id'] for command in screen['commands']
                if command['parameters'].get('check_id') == CHECK_ID] == [CHECK_ID]


def test_two_dcs_for_one_skill_ask_the_table_instead_of_guessing():
    """A phrase that fits two printed DCs is not a check the hero named."""
    place = dict(name='Гринест', description='', checks={
        'loc:l1:skill:stealth:10': dict(skill='stealth', dc=10, origin='master'),
        'loc:l2:skill:stealth:15': dict(skill='stealth', dc=15, origin='master')})
    state = dict(location='Гринест', locations=[place])
    assert match(state, 'Проверить: Скрытность') is None
    # The full id still names exactly one of them.
    assert match(state, 'проверить loc:l2:skill:stealth:15') == 'loc:l2:skill:stealth:15'
    single = dict(state, locations=[dict(place, checks={'loc:l1:skill:stealth:10': place['checks']['loc:l1:skill:stealth:10']})])
    assert match(single, 'Проверить: Скрытность') == 'loc:l1:skill:stealth:10'
    # A skill check is not its ability: asking for Ловкость must not roll
    # Скрытность against a DC the player never named.
    assert match(single, 'Проверить: Ловкость') is None
    # A save is named by its ability, and only a save answers to it.
    saving = dict(state, locations=[dict(place, checks={
        'loc:l1:saving_throw:dexterity:10': dict(ability='dexterity', dc=10, origin='master')})])
    assert match(saving, 'Проверить: Ловкость') == 'loc:l1:saving_throw:dexterity:10'
    # An ordinary sentence that happens to name a skill is not a check request.
    assert match(single, 'Я иду по улице и думаю о ловкости') is None


def test_a_printed_saving_throw_reaches_the_engine_as_a_save():
    """The id says the kind: a save keeps its proficiency and its save bucket."""
    from solo.nat20_prototype import PrototypeCheck
    from solo.source_checks import SourceCheck, to_prototype_check
    from solo.preparation_contracts import SourceQuote

    entry = to_prototype_check('loc:l1:saving_throw:dexterity:10', SourceCheck(
        ability='dexterity', dc=10, origin='source',
        source=SourceQuote(document='a' * 64, page=10, quote='каждый делает спасбросок Ловкости Сл 10')))
    assert (entry['kind'], entry['skill'], entry['ability']) == ('saving_throw', None, 'dex')
    # The entry is the shape the worker parses, not a lookalike dict.
    assert PrototypeCheck.model_validate(entry).kind == 'saving_throw'
