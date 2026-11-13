"""A03/F01: the eight-step master's last step is the launch's own first step.

The defect (audit 08.11, S55–S56): eight filled steps ended on «Выбрать героя
Nat20», which opened a *new empty* creation draft — so the hero, the start and
the three approved items stayed behind in «Новое приключение» with no
description and no materials.

These tests pin the single contract: one draft, its parts named explicitly
(``draft_id``, ``module_id``, ``hero_draft``, ``equipment``), a half-written
draft that says what is missing instead of opening an empty screen, and the
launch that starts the story with the hero's name, the start and 3 of 3 items.
"""
import hashlib
import uuid

from fastapi.testclient import TestClient

from solo.adventure_library import AUTO_SPLIT_KEY, AdventureLibrary
from solo.app import create_app
from solo.inventory import CATALOG
from solo.preparation import Draft
from solo.preview_mock import PreviewMock

DOCUMENT = hashlib.sha256(b'tiamat-fixture').hexdigest()
CHAPTERS = [(page, 'Глава 1. Город в огне') for page in (1, 2, 3)] + \
           [(page, 'Глава 2. Дорога к крепости') for page in (4, 5, 6)]
PAGES = {1: 'Пламя над Гринестом. Улица пуста, но из окон смотрят люди.',
         2: 'Дым закрывает солнце; на площади кричат стражники.',
         3: 'К утру город считает потери.',
         4: 'Дорога уходит на север, к заставе.',
         5: 'Ветер несёт пепел; впереди дым стелется низко.',
         6: 'На заставе горят факелы, стражи вдвое больше обычного.'}
ITEMS = ['longsword', 'shield', 'rope']


def app(tmp_path):
    return create_app(tmp_path / 'solo.sqlite3', PreviewMock(), experimental=True, offline_mock=True)


def book(library):
    """A bundle with one extracted book, cut into a ready module."""
    bundle = library.create('The Rise of Tiamat', {AUTO_SPLIT_KEY: True})['id']
    with library.db() as db:
        db.execute('INSERT OR IGNORE INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', max(PAGES)))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)',
                   (bundle, DOCUMENT, 'tiamat.pdf', 'book', ''))
        for number, text in PAGES.items():
            db.execute('INSERT OR IGNORE INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (DOCUMENT, number, str(number), text))
        for number, heading in CHAPTERS:
            db.execute('INSERT OR IGNORE INTO candidates(id,document,page,kind,title,body) VALUES(?,?,?,?,?,?)',
                       (hashlib.sha256(heading.encode() + bytes([number])).hexdigest()[:32],
                        DOCUMENT, number, 'chapter', heading, '{}'))
    library.auto_split(bundle, DOCUMENT)
    return bundle


def master_draft(client, bundle=None, module=None, hero='Лира', step=7):
    """The eight-step master as it stands on its last page, saved server-side."""
    if bundle is None:
        bundle = book(client.app.state.adventures)
        module = client.get('/api/adventure/library/' + bundle + '/modules').json()[0]['id']
    draft = client.post('/api/preparation/drafts', json={}).json()
    body = Draft.model_validate(draft['body']).model_dump(mode='json')
    body.update(name='Тиамат · хроника', description='Книга о драконах',
                adventure_bundle=bundle, adventure_module=module or '',
                source_kind='ready', prepared=True, background_confirmed=True,
                start_name='Гринест', opening=PAGES[1], equipment=list(ITEMS), step=step)
    body['hero'].update(name=hero, species='Человек', class_name='Воин', level=1,
                        hp=12, max_hp=12, ac=16, speed_ft=30,
                        background='Выросла в Гринесте и знает каждую улицу.')
    saved = client.put('/api/preparation/drafts/' + draft['id'],
                       json={'body': body, 'revision': draft['revision']})
    assert saved.status_code == 200, saved.text
    return draft['id'], bundle, module


def handoff(client, draft_id, **extra):
    draft = client.get('/api/preparation/drafts/' + draft_id).json()
    payload = dict(draft_id=draft_id, revision=draft['revision'],
                   module_id=draft['body']['adventure_module'],
                   hero_draft=draft['body']['hero'], equipment=draft['body']['equipment'])
    payload.update(extra)
    return client.post('/api/creation/drafts/' + draft_id + '/adopt', json=payload)


def test_the_last_step_keeps_one_draft_and_names_every_part(tmp_path):
    with TestClient(app(tmp_path)) as client:
        draft_id, bundle, module = master_draft(client)
        result = handoff(client, draft_id)
        assert result.status_code == 200, result.text
        result = result.json()
        assert result['ready'] is True, result
        state = result['handoff']
        # The four parts travel by name, not by guesswork.
        assert state['draft_id'] == draft_id
        assert state['module_id'] == module
        assert state['hero_draft']['name'] == 'Лира'
        assert state['equipment'] == ITEMS
        assert [row['name'] for row in state['equipment_items']] == [CATALOG[key]['name'] for key in ITEMS]
        assert state['start'] == 'Гринест' and state['opening'] == PAGES[1]
        assert state['book']['ready'] is True
        # One draft, not two: the launch opened the master's own record.
        assert [row['id'] for row in client.get('/api/preparation/drafts').json()] == [draft_id]
        assert [row['id'] for row in client.get('/api/creation/drafts').json()] == [draft_id]
        # The launch sees the same materials the master read.
        view = client.get('/api/creation/drafts/' + draft_id).json()
        assert [row['document'] for row in view['files']] == [DOCUMENT]
        assert view['handoff']['draft_id'] == draft_id and view['handoff']['ready'] is True
        # A second press hands over the same draft again and starts nothing twice.
        again = handoff(client, draft_id).json()
        assert again['ready'] is True and again['draft']['id'] == draft_id
        assert len(client.app.state.store.list_campaigns()) == 0


def test_a_half_written_draft_says_what_is_missing_and_is_not_migrated(tmp_path):
    with TestClient(app(tmp_path)) as client:
        draft = client.post('/api/preparation/drafts', json={}).json()
        result = client.post('/api/creation/drafts/' + draft['id'] + '/adopt', json={}).json()
        assert result['ready'] is False
        missing = {row['key']: row for row in result['missing']}
        assert {'hero_name', 'equipment', 'start_name', 'prepared', 'materials'} <= set(missing)
        # «Дозаполнить» needs the step, and the step is the master's own number.
        assert (missing['hero_name']['step'], missing['equipment']['step'],
                missing['start_name']['step'], missing['prepared']['step']) == (4, 6, 2, 3)
        assert 'Имя героя' in missing['hero_name']['text']
        # Nothing was migrated and nothing was created for it.
        assert client.get('/api/preparation/drafts/' + draft['id']).json()['body']['flow'] == 'legacy'
        assert client.get('/api/creation/drafts').json() == []
        # The launch refuses the half-written draft by naming the parts — never an
        # empty «Новое приключение».
        refused = client.get('/api/creation/drafts/' + draft['id'])
        assert refused.status_code == 409
        assert 'дозаполните' in refused.json()['detail']


def test_the_new_story_has_the_hero_the_start_and_three_items(tmp_path):
    with TestClient(app(tmp_path)) as client:
        draft_id, bundle, module = master_draft(client)
        assert handoff(client, draft_id).json()['ready'] is True
        build = client.get('/api/prototype/options').json()['default_build']
        created = client.post('/api/prototype/campaigns', json=dict(
            name='Лира', request_id='f01-' + uuid.uuid4().hex[:8], build=build,
            adventure='sandbox', preparation_draft=draft_id, module_id=module,
            hero_draft={'name': 'Лира'}, equipment=list(ITEMS), encounter_monsters=[]))
        assert created.status_code == 200, created.text
        created = created.json()
        state = client.app.state.store.campaign(created['id'])['state']
        # 1 of 3: the hero's name.
        assert state['hero']['name'] == 'Лира'
        # 2 of 3: the start the master named, with the master's opening.
        assert state['location'] == 'Гринест'
        assert state['locations'][0]['description'].startswith('Пламя над Гринестом')
        # 3 of 3: the items the player approved, all three of them.
        assert state['preparation']['equipment'] == ITEMS
        assert [row['id'] for row in created['equipment']] == ITEMS
        character = client.get('/api/campaigns/' + created['id'] + '/character').json()
        shown = [row['id'] for row in character['equipment']]
        assert set(ITEMS) <= set(shown), shown
        # The items are the master's, and they say so.
        assert [row.get('source') for row in character['equipment'] if row['id'] in ITEMS] == ['preparation'] * 3
        # One draft, one campaign — and the draft now belongs to it.
        assert [row['id'] for row in client.get('/api/preparation/drafts').json()] == [draft_id]
        assert client.get('/api/preparation/drafts/' + draft_id).json()['campaign'] == created['id']


def test_the_second_entry_from_the_book_gives_the_same_three_of_three(tmp_path):
    with TestClient(app(tmp_path)) as client:
        first, bundle, module = master_draft(client)
        assert handoff(client, first).json()['ready'] is True
        build = client.get('/api/prototype/options').json()['default_build']

        def start(draft_id):
            return client.post('/api/prototype/campaigns', json=dict(
                name='Лира', request_id='f01-' + uuid.uuid4().hex[:8], build=build,
                adventure='sandbox', preparation_draft=draft_id, module_id=module,
                hero_draft={'name': 'Лира'}, equipment=list(ITEMS), encounter_monsters=[]))

        one = start(first)
        assert one.status_code == 200, one.text
        # A second entry: the library card cuts a fresh draft for the same book and
        # module (that is what its «Создать героя» button does), and the launch is
        # the same contract — so the result is the same 3 of 3, not an empty game.
        second, _, _ = master_draft(client, bundle, module)
        assert handoff(client, second).json()['ready'] is True
        two = start(second)
        assert two.status_code == 200, two.text
        for response in (one, two):
            state = client.app.state.store.campaign(response.json()['id'])['state']
            assert state['hero']['name'] == 'Лира'
            assert state['location'] == 'Гринест'
            assert state['preparation']['equipment'] == ITEMS
        assert len(client.app.state.store.list_campaigns()) == 2


def test_a_changed_part_is_refused_before_anything_is_created(tmp_path):
    with TestClient(app(tmp_path)) as client:
        draft_id, bundle, module = master_draft(client)
        assert handoff(client, draft_id).json()['ready'] is True
        build = client.get('/api/prototype/options').json()['default_build']
        stale = client.post('/api/prototype/campaigns', json=dict(
            name='Лира', request_id='f01-' + uuid.uuid4().hex[:8], build=build,
            adventure='sandbox', preparation_draft=draft_id, module_id=module,
            hero_draft={'name': 'Совсем другой'}, equipment=list(ITEMS), encounter_monsters=[]))
        assert stale.status_code == 409 and 'Герой изменился' in stale.json()['detail']
        assert len(client.app.state.store.list_campaigns()) == 0
        wrong_items = client.post('/api/prototype/campaigns', json=dict(
            name='Лира', request_id='f01-' + uuid.uuid4().hex[:8], build=build,
            adventure='sandbox', preparation_draft=draft_id, module_id=module,
            hero_draft={'name': 'Лира'}, equipment=['club'], encounter_monsters=[]))
        assert wrong_items.status_code == 409 and 'Снаряжение изменилось' in wrong_items.json()['detail']
        assert len(client.app.state.store.list_campaigns()) == 0


def test_a_stale_master_tab_cannot_migrate_a_changed_draft(tmp_path):
    with TestClient(app(tmp_path)) as client:
        draft_id, bundle, module = master_draft(client)
        before = client.get('/api/preparation/drafts/' + draft_id).json()
        draft = client.app.state.preparation.get(draft_id)
        body = Draft.model_validate(draft['body'])
        body.equipment = ['longsword', 'shield']
        client.app.state.preparation.save(draft_id, body, draft['revision'])
        stale = client.post('/api/creation/drafts/' + draft_id + '/adopt', json=dict(
            draft_id=draft_id, revision=before['revision'],
            module_id=module, hero_draft={'name': 'Лира'}, equipment=list(ITEMS)))
        assert stale.status_code == 409 and 'Черновик изменился' in stale.json()['detail']
        assert client.get('/api/preparation/drafts/' + draft_id).json()['body']['flow'] == 'legacy'

def test_the_launch_opens_a_ready_master_draft_by_its_id(tmp_path):
    """The library card's «Создать героя» reads the draft by id, not by a view.

    Regression: ``get()`` returned the launch *view* from ``owned()`` and then
    indexed it as a saved draft (``KeyError: 'body'`` → 404), so the card could
    not open a finished master draft at all.
    """
    with TestClient(app(tmp_path)) as client:
        draft_id, bundle, module = master_draft(client)
        view = client.get('/api/creation/drafts/' + draft_id)
        assert view.status_code == 200, view.text
        view = view.json()
        assert view['id'] == draft_id
        assert view['handoff']['ready'] is True
        assert view['handoff']['module_id'] == module
        assert view['handoff']['book']['ready'] is True
        assert [row['document'] for row in view['files']] == [DOCUMENT]
        # The same draft was migrated, not copied.
        assert client.get('/api/preparation/drafts/' + draft_id).json()['body']['flow'] == 'simple'
        assert [row['id'] for row in client.get('/api/creation/drafts').json()] == [draft_id]


def test_a_finished_master_draft_is_listed_before_its_first_launch(tmp_path):
    """A saved finished draft is migrated, not hidden behind the master screen.

    The audit's own case: eight steps filled, the tab closed before the last press.
    The draft must appear in the player's library as «Готово к созданию героя»,
    and opening it must migrate that same draft instead of copying it.
    """
    with TestClient(app(tmp_path)) as client:
        draft_id, bundle, module = master_draft(client)
        rows = client.get('/api/creation/drafts').json()
        assert [row['id'] for row in rows] == [draft_id]
        assert rows[0]['status'] == 'ready'
        assert rows[0]['handoff']['ready'] is True
        assert rows[0]['handoff']['equipment'] == ITEMS
        drafts = client.get('/api/chronicles').json()['drafts']
        assert [(row['id'], row['status']) for row in drafts] == [(draft_id, 'ready')]
        # A listing is read-only: the draft is not migrated behind the player's back,
        # so an open master tab does not get a revision mismatch from a library view.
        saved = client.get('/api/preparation/drafts/' + draft_id).json()
        assert saved['body']['flow'] == 'legacy'
        assert rows[0]['revision'] == saved['revision']
        # Opening it by id migrates that same record in place; no second draft.
        opened = client.get('/api/creation/drafts/' + draft_id).json()
        assert opened['id'] == draft_id and opened['revision'] == saved['revision'] + 1
        assert client.get('/api/preparation/drafts/' + draft_id).json()['body']['flow'] == 'simple'
        assert [row['id'] for row in client.get('/api/preparation/drafts').json()] == [draft_id]
