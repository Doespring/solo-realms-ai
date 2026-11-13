"""A confirmed map or handout lands on the table because the player laid it out.

r151 cut the pictures out of the book, a person confirms one in the review screen
and r155 closes the chain: the phrase at the table names the material, the host
books the engine's own `handout` event, and the row is in `known_media` and on
the «Раздатки и карты» screen before the answer is read.

The material rows here are the ones the importer leaves behind (kind, page,
reviewed, sanitized, status); the confirmed name is what a person typed in the
review screen. Nothing in these tests reaches for a model: a reveal that needs
one would be a reveal the game cannot run offline.
"""
import hashlib
import json
import uuid

from fastapi.testclient import TestClient

from solo.adventure_engine import AdventureService
from solo.adventure_handout import asked, resolve
from solo.app import create_app
from solo.preview_mock import PreviewMock

DOCUMENT = hashlib.sha256(b'r155-confirmed-map-in-the-book').hexdigest()
MAP = 'map-camp'                 # confirmed, open, page 19 — «Карта лагеря»
PLAN = 'map-dungeon'             # confirmed, open, page 40 — «План подземелья»
LETTER = 'handout-letter'        # still a candidate, page 21 — printed caption only
MASTER = 'map-temple'            # confirmed but closed: the Master's own map


def app(tmp_path):
    return TestClient(create_app(tmp_path / 'solo.sqlite3', PreviewMock(), experimental=True, offline_mock=True))


def book(library):
    """A bundle holding one extracted book and the rows the importer leaves.

    Every page also carries its own `kind='page'` row — the whole scan, which is
    the page and not a card — exactly as the importer writes it, so a reveal can
    never be answered by the page instead of by the picture cut out of it.
    """
    bundle = library.create('Клад Королевы Драконов', {'edition': '2024'})['id']
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', 40))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)', (bundle, DOCUMENT, 'book.pdf', 'book', ''))
        for number in range(1, 41):
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (DOCUMENT, number, str(number), 'Страница %d книги про лагерь.' % number))
            db.execute('INSERT INTO materials(id,document,page,kind,scope,status) VALUES(?,?,?,?,?,?)',
                       ('page-%d' % number, DOCUMENT, number, 'page', 'gm', 'page'))
        rows = [
            # id, page, region, kind, scope, printed name, reviewed, sanitized, status, caption
            (MAP, 19, 'r1', 'map', 'player', 'Карта лагеря', 1, 1, 'confirmed', ''),
            (PLAN, 40, 'r2', 'map', 'player', 'План подземелья', 1, 1, 'confirmed', ''),
            (LETTER, 21, 'r3', 'handout', 'gm', '', 0, 0, 'candidate', 'Раздатка: письмо барона'),
            (MASTER, 30, 'r4', 'map', 'gm', 'Тайная карта храма', 1, 0, 'confirmed', ''),
        ]
        for id, page, region, kind, scope, name, reviewed, sanitized, status, caption in rows:
            db.execute('INSERT INTO materials(id,document,page,region,kind,scope,public_name,reviewed,'
                       'sanitized,status,caption) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (id, DOCUMENT, page, region, kind, scope, name, reviewed, sanitized, status, caption))
    return bundle


def definition():
    """One reviewed module over the book; no events, so a reveal is the only thing that happens."""
    return dict(id='r155', title='Лагерь налетчиков', ruleset='dnd2024', ruleset_version=1,
                source_ranges=[dict(document=DOCUMENT, first_page=1, last_page=40)])


def lay_out(client, bundle):
    """A player campaign with that book's reviewed scenario attached to it."""
    created = client.post('/api/prototype/campaigns', json=dict(
        name='Лира', request_id='r155-' + uuid.uuid4().hex[:8],
        build=client.get('/api/prototype/options').json()['default_build'],
        adventure='sandbox', encounter_monsters=[]))
    assert created.status_code == 200, created.text
    campaign = created.json()['id']
    AdventureService(client.app.state.store, client.app.state.adventures).attach(
        campaign, bundle, 'r155-attach', True)
    return campaign


def turn(client, campaign, text, request_id=None):
    version = client.get('/api/campaigns/' + campaign).json()['version']
    return client.post('/api/campaigns/' + campaign + '/command-turn', json=dict(
        request_id=request_id or 'r155-turn-' + uuid.uuid4().hex[:8], text=text, expected_version=version))


def test_the_confirmed_map_named_out_loud_is_on_the_table_and_in_known_media(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        campaign = lay_out(client, bundle)
        store = client.app.state.store
        assert store.campaign(campaign)['state']['adventure']['known_media'] == []

        answer = turn(client, campaign, 'Показываю игрокам карту лагеря').json()
        assert answer['status'] == 'resolved', answer
        # The player is told what is on the table, with the page the book printed.
        assert answer['narrative'] == '«Карта лагеря» на столе, стр. 19.', answer['narrative']
        state = store.campaign(campaign)['state']['adventure']
        assert state['known_media'] == [MAP]
        assert state['media_records'][MAP]['name'] == 'Карта лагеря'
        assert state['media_bundles'][MAP] == bundle
        # The reveal is the engine's own event, with its page of origin.
        record = state['journal'][-1]
        assert record['category'] == 'handout' and record['effects'] == [{'kind': 'handout', 'target': MAP}]
        assert record['source'] == {'document': DOCUMENT, 'page': 19, 'review': 'confirmed'}
        assert answer['events'][0]['results'][0]['kind'] == 'handout'
        # The material is on «Раздатки и карты»: the player projection carries it.
        public = client.get('/api/campaigns/' + campaign).json()['state']['adventure']
        assert [(row['id'], row['name'], row['kind']) for row in public['materials']] == [(MAP, 'Карта лагеря', 'map')]


def test_the_page_alone_finds_the_material_when_the_title_is_not_typed(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        campaign = lay_out(client, bundle)

        answer = turn(client, campaign, 'Раздаю карту со стр. 19').json()
        assert answer['status'] == 'resolved', answer
        assert 'стр. 19' in answer['narrative']
        assert client.app.state.store.campaign(campaign)['state']['adventure']['known_media'] == [MAP]


def test_a_phrase_without_a_name_or_a_page_lists_what_the_campaign_has(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        campaign = lay_out(client, bundle)

        answer = turn(client, campaign, 'Показываю игрокам карту').json()
        # Not guessed: the only map in the book is not silently chosen for the table.
        assert answer['status'] == 'unsupported', answer
        assert 'Вот что есть' in answer['narrative'] and '«Карта лагеря» (стр. 19)' in answer['narrative']
        assert client.app.state.store.campaign(campaign)['state']['adventure']['known_media'] == []


def test_a_material_nobody_confirmed_is_not_revealed(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        campaign = lay_out(client, bundle)

        answer = turn(client, campaign, 'Раздаю письмо барона').json()
        assert answer['status'] == 'unsupported', answer
        assert 'ждёт подтверждения' in answer['narrative']
        state = client.app.state.store.campaign(campaign)['state']['adventure']
        assert state['known_media'] == [] and LETTER not in state['media_records']


def test_the_masters_own_map_stays_closed(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        campaign = lay_out(client, bundle)

        answer = turn(client, campaign, 'Показываю тайную карту храма').json()
        assert answer['status'] == 'unsupported', answer
        assert 'Мастера' in answer['narrative']
        state = client.app.state.store.campaign(campaign)['state']['adventure']
        assert state['known_media'] == [] and MASTER not in state['media_records']


def test_the_same_turn_repeated_books_the_material_once(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        campaign = lay_out(client, bundle)

        request_id = 'r155-twice-' + uuid.uuid4().hex[:8]
        first = turn(client, campaign, 'Показываю игрокам карту лагеря', request_id).json()
        repeat = turn(client, campaign, 'Показываю игрокам карту лагеря', request_id).json()
        assert repeat['status'] == 'resolved' and repeat['narrative'] == first['narrative']
        state = client.app.state.store.campaign(campaign)['state']['adventure']
        assert state['known_media'] == [MAP]
        assert [row['id'] for row in state['journal']].count('handout-' + MAP) == 1


def test_a_second_reveal_of_the_same_map_says_it_is_already_there(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        campaign = lay_out(client, bundle)
        turn(client, campaign, 'Показываю игрокам карту лагеря')

        again = turn(client, campaign, 'Показываю игрокам карту лагеря').json()
        assert again['status'] == 'resolved' and 'уже на столе' in again['narrative']
        state = client.app.state.store.campaign(campaign)['state']['adventure']
        assert state['known_media'] == [MAP]


def test_a_refusal_does_not_swallow_the_next_turn(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        campaign = lay_out(client, bundle)

        refused = turn(client, campaign, 'Раздаю письмо барона').json()
        assert refused['status'] == 'unsupported', refused
        # r155 live run: the refusal was stored as an open clarification, so the
        # very next sentence was read as an answer to it and never reached the
        # material at all. An answer is not a question.
        assert client.app.state.store.open_question(campaign) is None
        answer = turn(client, campaign, 'Показываю игрокам карту лагеря').json()
        assert answer['status'] == 'resolved' and 'стр. 19' in answer['narrative'], answer


def test_two_materials_with_the_same_printed_name_wait_for_the_page(tmp_path):
    # The same caption cut twice out of one book (a full map and its inset) is
    # confirmed twice; a phrase that names both cannot choose between them.
    materials = [
        dict(id='a', page=19, kind='map', public_name='Карта лагеря', status='confirmed',
             scope='player', reviewed=1, sanitized=1),
        dict(id='b', page=40, kind='map', public_name='Карта лагеря', status='confirmed',
             scope='player', reviewed=1, sanitized=1),
    ]
    ambiguous = resolve(materials, **asked('Показываю игрокам карту лагеря'))
    assert ambiguous['status'] == 'ambiguous' and {row['id'] for row in ambiguous['materials']} == {'a', 'b'}
    assert 'Назовите страницу' in ambiguous['text']
    assert resolve(materials, **asked('Показываю игрокам карту лагеря со стр. 19'))['material']['id'] == 'a'


def test_a_book_campaign_without_its_scenario_says_the_map_has_nowhere_to_go(tmp_path):
    """The common product state: the book is bound, the scenario is not attached.

    `known_media` belongs to the scenario and the «Раздатки и карты» screen is its
    projection, so a confirmed map of a merely bound book has nowhere to be shown.
    Saying that is the honest answer; a narrator improvising the map is not.
    """
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        module = dict(id='r155-bound', title='Лагерь налетчиков', ruleset='dnd2024', ruleset_version=1,
                      reviewed=True, unsupported=[],
                      source_ranges=[dict(document=DOCUMENT, first_page=1, last_page=40)])
        with library.db() as db:
            db.execute('INSERT INTO adventure_modules VALUES(?,?,?,1)', (bundle, module['id'], json.dumps(module)))
        draft = client.post('/api/preparation/drafts', json={}).json()
        body = draft['body']
        body.update(adventure_bundle=bundle, ruleset='dnd2024')
        saved = client.put('/api/preparation/drafts/' + draft['id'], json={'body': body, 'revision': draft['revision']})
        assert saved.status_code == 200, saved.text
        world = client.post('/api/preparation/drafts/' + draft['id'] + '/world', json={}).json()
        assert world['bound'] is True, world
        created = client.post('/api/prototype/campaigns', json=dict(
            name='Лира', request_id='r155-bound-' + uuid.uuid4().hex[:8],
            build=client.get('/api/prototype/options').json()['default_build'],
            adventure='sandbox', preparation_draft=draft['id'], encounter_monsters=[]))
        assert created.status_code == 200, created.text
        campaign = created.json()['id']
        state = client.app.state.store.campaign(campaign)['state']
        assert 'adventure' not in state and state['preparation']['library']['bundle'] == bundle

        answer = turn(client, campaign, 'Показываю игрокам карту лагеря').json()
        assert answer['status'] == 'unsupported', answer
        assert 'сценарий ещё не подключён' in answer['narrative'], answer


def test_a_phrase_about_something_else_is_left_to_the_ordinary_turn(tmp_path):
    # No material noun and no page: the host must not claim the phrase, or the
    # narrator would lose a scene description it used to answer.
    assert asked('Показываю игрокам сцену') is None
    assert asked('Смотрю на карту лагеря') is None
    assert asked('Иду в лагерь') is None
    assert asked('Показываю игрокам карту') == dict(key=[], page=None)
