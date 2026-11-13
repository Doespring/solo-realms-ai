"""r166: a long session does not fall apart, and it survives save/restore.

The prototype is played in one sitting: a player answers twenty times without
reloading the page, then saves, undoes a turn and loads the save back. Three
things have to hold for the whole twenty, not for one lucky turn:

* the version only grows, and a repeated request id is answered from the record
  instead of running a second time (no double effect, no phantom turn);
* the pending effects a reviewed book queued are executed by the next screen
  turn and leave nothing behind in `deferred`;
* a confirmed map goes onto the table once, and the second mention says it is
  already there instead of booking the same handout twice.

`save -> undo -> restore -> next turn` is the other half: a checkpoint has to
give back exactly the state it was taken from, and the campaign has to keep
playing after it (r166 live run, 20 turns, 0 errors, 0 version regressions).

The book here is synthetic on purpose: a real PDF would make the test depend on
a file that is not in the repository. The places it prints are the ones the
importer leaves behind, and every effect cites a page of the same document, so
the scenario is reviewed exactly like a cut-out module.
"""
import hashlib
import json
import uuid

from fastapi.testclient import TestClient

from solo.adventure_engine import AdventureService
from solo.app import create_app
from solo.preview_mock import PreviewMock

DOCUMENT = hashlib.sha256(b'r166-long-session').hexdigest()
MAP = 'r166-map'          # printed map, confirmed for the players, page 19
LETTER = 'r166-letter'    # printed handout nobody confirmed, page 21
PAGES = 40


def app(tmp_path):
    return TestClient(create_app(tmp_path / 'solo.sqlite3', PreviewMock(),
                                 experimental=True, offline_mock=True))


def book(library):
    """One imported book: pages, the pictures cut out of them, and two materials."""
    bundle = library.create('r166 · Клад', {'edition': '2024'})['id']
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', PAGES))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)', (bundle, DOCUMENT, 'book.pdf', 'book', ''))
        for number in range(1, PAGES + 1):
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (DOCUMENT, number, str(number), 'Страница %d книги про лагерь.' % number))
            db.execute('INSERT INTO materials(id,document,page,kind,scope,status) VALUES(?,?,?,?,?,?)',
                       ('page-%d' % number, DOCUMENT, number, 'page', 'gm', 'page'))
        db.execute('INSERT INTO materials(id,document,page,region,kind,scope,public_name,reviewed,'
                   'sanitized,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (MAP, DOCUMENT, 19, 'r1', 'map', 'player', 'Карта лагеря', 1, 1, 'confirmed'))
        db.execute('INSERT INTO materials(id,document,page,region,kind,scope,caption,reviewed,'
                   'sanitized,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (LETTER, DOCUMENT, 21, 'r2', 'handout', 'gm', 'Письмо барона', 0, 0, 'candidate'))
    return bundle


def definition():
    """A reviewed module: three printed effects and one table, no invented prose."""
    evidence = dict(document=DOCUMENT, page=1, review='confirmed')
    gate = dict(category='timed', id='gate', after_minutes=0, source=evidence,
                effects=[dict(kind='check', ability='wisdom', dc=12, target='gate_open',
                              text='Замечен ли след у калитки')])
    toll = dict(category='timed', id='toll', after_minutes=0,
                source=dict(document=DOCUMENT, page=2, review='confirmed'),
                effects=[dict(kind='resource', target='hit_dice', value=-1, text='Ночь без отдыха')])
    purse = dict(category='timed', id='purse', after_minutes=0,
                 source=dict(document=DOCUMENT, page=3, review='confirmed'),
                 effects=[dict(kind='table', target='purse-table', text='Что в кошеле')])
    table = dict(id='purse-table', formula='1d4',
                 source=dict(document=DOCUMENT, page=3, review='confirmed'),
                 rows=[dict(low=1, high=4, label='Кошель',
                            effects=[dict(kind='reward', target='purse-gold', text='Награда за находку')])])
    # The reward the table prints has to name a progression entry of this very
    # book, otherwise the table rolls and cannot be booked: r166 measured the
    # effect staying in `deferred` turn after turn until this was added.
    rewards = dict(mode='experience', rewards=[dict(id='purse-gold', kind='experience', amount=50,
                                                    page=3, reason='Награда напечатана в книге')])
    return dict(id='r166', title='Лагерь налетчиков', ruleset='dnd2024', ruleset_version=1,
                source_ranges=[dict(document=DOCUMENT, first_page=1, last_page=PAGES)],
                events=[gate, toll, purse], tables=[table], progression=rewards)


def table(tmp_path):
    """A Nat20 campaign with that book attached, ready to be played."""
    with app(tmp_path) as client:
        store = client.app.state.store
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        created = client.post('/api/prototype/campaigns', json=dict(
            name='Рив', request_id='r166-' + uuid.uuid4().hex[:8],
            build=client.get('/api/prototype/options').json()['default_build'],
            adventure='sandbox', encounter_monsters=[]))
        assert created.status_code == 200, created.text
        campaign = created.json()['id']
        AdventureService(store, library).attach(campaign, bundle, 'r166-attach', True)
        assert store.campaign(campaign)['state']['adventure']['deferred'], \
            'the reviewed module queues its printed effects for the next screen turn'
    return client, store, campaign


def snapshot(client, store, campaign):
    """The campaign as the profile holds it, plus what the player screen shows.

    `known_media` and `deferred` are the campaign's own facts and are not part of
    the HTTP projection: the screen is given `materials`, so reading the public
    answer alone silently reports "no map, nothing pending" for every turn.
    """
    current = store.campaign(campaign)
    state = current['state']
    adventure = state.get('adventure') or {}
    hero = state.get('hero') or {}
    history = store.history(campaign, limit=1000)
    public = client.get('/api/campaigns/' + campaign).json()['state'].get('adventure') or {}
    return dict(version=int(current['version']),
                applied=len([row for row in history if not row['undone']]),
                journal=len(history),
                known_media=list(adventure.get('known_media') or []),
                public_materials=[row['id'] for row in public.get('materials') or []],
                deferred=[row.get('kind') for row in (adventure.get('deferred') or [])],
                hp=hero.get('hp'),
                state_hash=hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest())


def turn(client, campaign, text, request_id=None):
    version = client.get('/api/campaigns/' + campaign).json()['version']
    return client.post('/api/campaigns/' + campaign + '/command-turn', json=dict(
        request_id=request_id or 'r166-' + uuid.uuid4().hex[:10], text=text, expected_version=version))


def command(client, campaign, action, request_id=None):
    version = client.get('/api/campaigns/' + campaign).json()['version']
    return client.post('/api/campaigns/' + campaign + '/commands', json=dict(
        request_id=request_id or 'r166-' + uuid.uuid4().hex[:10], expected_version=version, action=action))


def offered(client, campaign):
    screen = client.get('/api/campaigns/' + campaign + '/screen').json()
    return [row['parameters'] for row in screen.get('commands') or []]


def test_twenty_turns_keep_one_version_going_up_and_book_a_reveal_once(tmp_path):
    client, store, campaign = table(tmp_path)
    versions = []
    reveal = 'Показываю игрокам карту лагеря'
    replayed = 'r166-reveal-once'

    with client:
        # One turn per kind of thing a table does, twenty of them, in one sitting.
        kinds = ['осмотр', 'проверка', 'проверка', 'отдых', 'карта', 'повтор карты',
                 'неподтверждённый', 'осмотр', 'проверка', 'отдых',
                 'карта', 'осмотр', 'проверка', 'неподтверждённый', 'осмотр',
                 'проверка', 'карта', 'осмотр', 'проверка', 'осмотр']
        for number, kind in enumerate(kinds, start=1):
            before = snapshot(client, store, campaign)
            available = [row.get('kind') for row in offered(client, campaign)]
            if kind == 'карта':
                response = turn(client, campaign, reveal, replayed if number == 5 else None)
            elif kind == 'повтор карты':
                # The same sentence under the same request id: the record answers.
                response = turn(client, campaign, reveal, 'r166-second-reveal')
            elif kind == 'неподтверждённый':
                response = turn(client, campaign, 'Раздаю письмо барона')
            elif kind == 'проверка' and 'check' in available:
                action = next(row for row in offered(client, campaign) if row['kind'] == 'check')
                response = command(client, campaign, action)
            elif kind == 'отдых' and 'short_rest' in available:
                action = next(row for row in offered(client, campaign) if row['kind'] == 'short_rest')
                response = command(client, campaign, action)
            else:
                response = turn(client, campaign, 'Осматриваюсь')
            assert response.status_code == 200, (number, kind, response.text)
            body = response.json()
            after = snapshot(client, store, campaign)
            versions.append(after['version'])
            # Nothing silently rolled back, and no turn left its own refuse open.
            assert after['version'] >= before['version'], (number, kind, before, after)
            if number > 1:
                assert after['deferred'] == [], (number, 'deferred effects were left pending', after['deferred'])
            assert after['hp'] is None or 0 <= after['hp'], (number, after['hp'])
            assert body.get('status') in ('resolved', 'observation', 'unsupported',
                                          'clarification', 'conversation', 'rejected'), body.get('status')

        assert len(versions) == 20
        # A repeated request id is answered from the record: same numbers, no turn.
        once = snapshot(client, store, campaign)
        replay = turn(client, campaign, reveal, replayed)
        assert replay.status_code == 200 and replay.json().get('replayed') is True, replay.text
        assert snapshot(client, store, campaign)['version'] == once['version']
        assert snapshot(client, store, campaign)['applied'] == once['applied']

        # The confirmed map was laid out once and stayed one row.
        adventure = store.campaign(campaign)['state']['adventure']
        assert adventure['known_media'] == [MAP], adventure['known_media']
        handouts = [row for row in adventure['journal'] if row.get('category') == 'handout']
        assert len(handouts) == 1, handouts
        assert handouts[0]['effects'] == [{'kind': 'handout', 'target': MAP}]
        # A material nobody confirmed was never revealed by a repeated phrase.
        assert LETTER not in adventure['media_records'] or not adventure['media_records'][LETTER].get('status') == 'confirmed'


def test_save_undo_restore_gives_back_the_saved_state_and_the_game_goes_on(tmp_path):
    client, store, campaign = table(tmp_path)
    with client:
        assert turn(client, campaign, 'Осматриваюсь').status_code == 200
        # A screen turn is what executes the book's queued effects (r159); a purely
        # narrative turn does not touch the queue, and this test wants them drained.
        check = next(row for row in offered(client, campaign) if row['kind'] == 'check')
        assert command(client, campaign, check).status_code == 200
        assert turn(client, campaign, 'Показываю игрокам карту лагеря').status_code == 200
        saved = snapshot(client, store, campaign)
        assert saved['deferred'] == [], saved['deferred']
        save = client.post('/api/campaigns/' + campaign + '/saves', json={'name': 'r166 · сохранение'})
        assert save.status_code == 200, save.text
        save_id = save.json()['id']

        # A turn after the save changes the table: the undo has something to undo.
        moved = turn(client, campaign, 'Осматриваюсь')
        assert moved.status_code == 200
        after_turn = snapshot(client, store, campaign)
        assert after_turn['applied'] > saved['applied']

        undone = client.post('/api/campaigns/' + campaign + '/undo')
        assert undone.status_code == 200, undone.text
        after_undo = snapshot(client, store, campaign)
        assert after_undo['applied'] == saved['applied'], (after_undo, saved)
        assert after_undo['version'] > after_turn['version'], 'undo is a new state, not a rewound counter'

        restored = client.post('/api/campaigns/' + campaign + '/restore/' + save_id)
        assert restored.status_code == 200, restored.text
        after_restore = snapshot(client, store, campaign)
        # The state is the saved one, byte for byte; the version keeps growing
        # because a load is itself a new operation on the same campaign.
        assert after_restore['state_hash'] == saved['state_hash']
        assert after_restore['applied'] == saved['applied']
        assert after_restore['known_media'] == saved['known_media'] == [MAP]
        assert after_restore['version'] > after_undo['version']
        assert after_restore['deferred'] == []

        # And it is a live session, not a museum piece.
        again = turn(client, campaign, 'Осматриваюсь')
        assert again.status_code == 200, again.text
        assert snapshot(client, store, campaign)['version'] == after_restore['version'] + 1


def test_a_confirmed_map_is_laid_out_even_while_a_question_is_open(tmp_path):
    """r155 taught that an answer must not swallow the next turn.

    The same holds the other way: a phrase the host resolves without a model — a
    map named out loud — must not be read as an answer to the model's open
    question. In the r166 live run the offline mock asked «Укажите проверку»,
    and the next four turns (the map and an unconfirmed handout among them) were
    all answered with that same question; `known_media` stayed empty.
    """
    client, store, campaign = table(tmp_path)
    with client:
        asked = turn(client, campaign, 'Проверяю Восприятие')
        assert asked.status_code == 200 and asked.json()['status'] == 'clarification', asked.text
        assert store.open_question(campaign) is not None

        laid = turn(client, campaign, 'Показываю игрокам карту лагеря')
        assert laid.status_code == 200, laid.text
        assert laid.json()['status'] == 'resolved', laid.text
        assert 'стр. 19' in laid.json()['narrative'], laid.json()['narrative']
        assert store.campaign(campaign)['state']['adventure']['known_media'] == [MAP]
