"""A material is found by what is drawn on it, not only by what the book prints.

r168 answered «ту карту, где река» from the printed name, the caption and the
text a person marked public — and on a real book that is a lot of nothing: a map
cut out of a page is captioned «Карта», and the words around it belong to the
neighbours. r190 adds the second source of words: one model reading of the
picture itself — `type` (map/handout/portrait/scheme) and `features` (река, мост,
лес) — stored on the material row.

The model only marks up; it never chooses at the table. The search stays
deterministic and keeps every gate of r168: only a confirmed and sanitised
material is laid out, a candidate is never revealed, and a description fitting
more than one material becomes a question.
"""
import contextlib
import hashlib
import json
import uuid

from fastapi.testclient import TestClient

from solo.adventure_engine import AdventureService
from solo.adventure_handout import asked, resolve
from solo.app import create_app
from solo.material_markup import MaterialMarkup, searchable
from solo.preview_mock import PreviewMock

DOCUMENT = hashlib.sha256(b'r190-free-description-markup').hexdigest()


def material(id, page, *, kind='map', name='', caption='', text='', status='confirmed',
             scope='player', reviewed=1, sanitized=1, markup=None):
    """One row the importer leaves behind, with what a person and the model added."""
    return dict(id=id, document=DOCUMENT, page=page, kind=kind, scope=scope, status=status,
                public_name=name, caption=caption, public_text=text, reviewed=reviewed,
                sanitized=sanitized, markup=json.dumps(markup, ensure_ascii=False)
                if markup is not None else '')


# A map whose sheet prints nothing but «Карта»: only the markup says what it shows.
RIVER = material('map-river', 19, name='Карта лагеря', caption='Карта',
                 markup=dict(type='map', features=['река', 'мост', 'лагерь'],
                             summary='План лагеря налетчиков у реки.'))
# A second map with no river at all.
FOREST = material('map-forest', 40, name='План чащи', caption='Карта',
                  markup=dict(type='map', features=['лес', 'тропа', 'завал']))
# A picture the importer stored as an illustration; the markup knows it is a map.
DRAWN = material('ill-map', 52, kind='illustration', name='Схема подвала', caption='',
                 markup=dict(type='map', features=['подвал', 'колодец']))
LETTER = material('letter-duffit', 21, kind='handout', name='Письмо Даффита',
                  markup=dict(type='handout', features=['письмо', 'печать', 'долг']))


def test_the_words_of_the_markup_find_a_map_the_book_never_described():
    found = resolve([RIVER, FOREST, LETTER], **asked('Показываю игрокам ту карту, где река'))
    assert found['status'] == 'found', found
    assert found['material']['id'] == 'map-river'


def test_a_word_two_materials_share_becomes_a_question_not_a_choice():
    ford = material('map-ford', 44, name='План брода', caption='Карта',
                    markup=dict(type='map', features=['река', 'брод']))
    ambiguous = resolve([RIVER, ford, LETTER], **asked('Показываю игрокам ту карту, где река'))
    assert ambiguous['status'] == 'ambiguous', ambiguous
    assert {row['id'] for row in ambiguous['materials']} == {'map-river', 'map-ford'}
    assert 'Какой именно' in ambiguous['text']


def test_two_words_of_the_markup_settle_what_one_word_could_not():
    found = resolve([RIVER, FOREST], **asked('Показываю карту, где река и мост'))
    assert found['status'] == 'found' and found['material']['id'] == 'map-river'


def test_a_map_stored_as_an_illustration_answers_the_word_map():
    # The importer cut the picture out of a text column, so its kind is
    # «illustration»; the markup says it is a map, and «карту» must find it.
    found = resolve([DRAWN, LETTER], **asked('Показываю карту, где колодец'))
    assert found['status'] == 'found', found
    assert found['material']['id'] == 'ill-map'


def test_a_bare_noun_refuses_and_lists_five_of_what_the_module_has():
    many = [material('m%d' % n, 60 + n, name='Карта %d' % n, caption='Карта',
                     markup=dict(type='map', features=['зал']))
            for n in range(7)]
    refused = resolve(many, **asked('Показываю игрокам карту'))
    assert refused['status'] == 'missing', refused
    assert refused['text'].count('(стр. ') == 5 and 'и ещё 2' in refused['text'], refused['text']


def test_a_marked_up_candidate_is_named_by_page_and_nothing_else():
    waiting = material('cand-river', 23, name='', caption='Письмо барона Даффита', status='candidate',
                       reviewed=0, sanitized=0,
                       markup=dict(type='handout', features=['река', 'печать', 'долг']))
    answer = resolve([RIVER, waiting], **asked('Раздаю письмо, где река и долг'))
    assert answer['status'] == 'pending', answer
    assert 'ждёт подтверждения' in answer['text'] and 'стр. 23' in answer['text']
    assert 'барон' not in answer['text'].lower() and 'Даффит' not in answer['text']


def test_markup_of_a_master_material_never_prints_its_words():
    closed = material('gm-map', 70, name='Карта логова', caption='Карта', scope='gm', sanitized=0,
                      markup=dict(type='map', features=['логово', 'дракон']))
    answer = resolve([RIVER, closed], **asked('Показываю карту, где дракон'))
    assert answer['status'] == 'closed', answer
    assert 'стр. 70' in answer['text'] and 'дракон' not in answer['text'].lower()


# --- the markup itself: one picture in, words out -----------------------------


class Analyst:
    """A mark-up reader that answers from the facts it was given, no network."""

    def __init__(self, answer, failure=None):
        self.answer, self.failure = answer, failure
        self.metrics = []

    def configured(self):
        return ('offline', '')

    def scope(self, campaign, turn_ref):
        return contextlib.nullcontext()

    def contract_error(self, code):
        pass

    async def stream(self, system, user, **kwargs):
        if self.failure:
            raise RuntimeError(self.failure)
        facts = json.loads(user[0]['text'] if isinstance(user, list) else user)
        yield json.dumps(self.answer(facts), ensure_ascii=False)


def library_for(tmp_path, rows):
    from solo.adventure_library import AdventureLibrary
    library = AdventureLibrary(tmp_path / 'adv')
    bundle = library.create('Лагерь', {'edition': '2024'})['id']
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', 80))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)', (bundle, DOCUMENT, 'book.pdf', 'book', ''))
        for row in rows:
            db.execute('INSERT INTO materials(id,document,page,region,kind,scope,caption) '
                       'VALUES(?,?,?,?,?,?,?)',
                       (row['id'], DOCUMENT, row['page'], row['id'], row['kind'], 'gm', row['caption']))
    return library, bundle


def test_the_model_answer_is_stored_and_the_search_reads_it(tmp_path):
    library, bundle = library_for(tmp_path, [dict(id='map-river', page=19, kind='map', caption='Карта')])
    marker = MaterialMarkup(library, Analyst(lambda facts: dict(
        type='map', features=['река', 'мост'], summary='План лагеря у реки.')))
    row = __import__('asyncio').run(marker.build('map-river', 'r190-build', bundle))
    assert row['markup_state'] == 'ready', row
    stored = json.loads(row['markup'])
    assert stored['type'] == 'map' and stored['features'] == ['река', 'мост']
    assert stored['version'] == 1 and stored['prompt'] == 'material_markup_v1'
    assert 'река' in searchable(row) and 'лагеря' in searchable(row)


def test_an_answer_off_the_schema_is_not_a_markup(tmp_path):
    library, bundle = library_for(tmp_path, [dict(id='map-river', page=19, kind='map', caption='Карта')])
    marker = MaterialMarkup(library, Analyst(lambda facts: dict(type='сокровище', features='река')))
    try:
        __import__('asyncio').run(marker.build('map-river', 'r190-bad', bundle))
    except ValueError as exc:
        assert 'не по схеме' in str(exc), exc
    else:
        raise AssertionError('схема не проверяется')
    row = library.material('map-river')
    assert row['markup'] == '' and row['markup_state'] == 'failed' and row['markup_note']


def test_a_failed_read_leaves_the_printed_name_answering(tmp_path):
    library, bundle = library_for(tmp_path, [dict(id='map-river', page=19, kind='map', caption='Карта')])
    library.set_material('map-river', scope='player', name='Карта лагеря', confirmed=True, sanitized=True)
    marker = MaterialMarkup(library, Analyst(lambda facts: {}, failure='sse_503 от провайдера'))
    try:
        __import__('asyncio').run(marker.build('map-river', 'r190-fail', bundle))
    except ValueError as exc:
        assert 'sse_503' in str(exc), exc
    else:
        raise AssertionError('отказ провайдера проглочен')
    row = library.material('map-river')
    assert row['markup_state'] == 'failed' and 'sse_503' in row['markup_note']
    assert resolve([row], **asked('Показываю карту лагеря'))['status'] == 'found'


# --- the same path through the host, not only through resolve() ---------------


class MarkupMock(PreviewMock):
    """Offline mock that also answers the picture reader, keyed by the page."""

    def __init__(self, answers):
        super().__init__()
        self.answers = answers

    def configured(self):
        return ('offline', '')

    def scope(self, campaign, turn_ref):
        return contextlib.nullcontext()

    def contract_error(self, code):
        pass

    async def stream(self, system, user, **kwargs):
        if kwargs.get('mode') == 'pdf_analyst' and 'размечаешь' in str(system):
            facts = json.loads(user[0]['text'] if isinstance(user, list) else user)
            yield json.dumps(self.answers(int(facts['page'])), ensure_ascii=False)
            return
        async for part in super().stream(system, user, **kwargs):
            yield part


def app(tmp_path, answers):
    return TestClient(create_app(tmp_path / 'solo.sqlite3', MarkupMock(answers),
                                 experimental=True, offline_mock=True))


def book(library):
    """The book r151 leaves behind: pages, one map per page, captions only."""
    bundle = library.create('Клад Королевы Драконов', {'edition': '2024'})['id']
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', 60))
        db.execute('INSERT INTO bundle_files VALUES(?,?,?,?,?)', (bundle, DOCUMENT, 'book.pdf', 'book', ''))
        for number in range(1, 61):
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (DOCUMENT, number, str(number), 'Страница %d книги про лагерь.' % number))
            db.execute('INSERT INTO materials(id,document,page,kind,scope,status) VALUES(?,?,?,?,?,?)',
                       ('page-%d' % number, DOCUMENT, number, 'page', 'gm', 'page'))
        for id, page in (('map-camp', 19), ('map-wood', 40)):
            db.execute('INSERT INTO materials(id,document,page,region,kind,scope,caption) '
                       'VALUES(?,?,?,?,?,?,?)', (id, DOCUMENT, page, id, 'map', 'gm', 'Карта'))
    return bundle


def definition():
    return dict(id='r190', title='Лагерь налетчиков', ruleset='dnd2024', ruleset_version=1,
                source_ranges=[dict(document=DOCUMENT, first_page=1, last_page=60)])


def campaign(client, bundle):
    created = client.post('/api/prototype/campaigns', json=dict(
        name='Лира', request_id='r190-' + uuid.uuid4().hex[:8],
        build=client.get('/api/prototype/options').json()['default_build'],
        adventure='sandbox', encounter_monsters=[]))
    assert created.status_code == 200, created.text
    id = created.json()['id']
    AdventureService(client.app.state.store, client.app.state.adventures).attach(id, bundle, 'r190-attach', True)
    return id


def turn(client, id, text):
    version = client.get('/api/campaigns/' + id).json()['version']
    return client.post('/api/campaigns/' + id + '/command-turn', json=dict(
        request_id='r190-turn-' + uuid.uuid4().hex[:8], text=text, expected_version=version))


def answers(page):
    return {19: dict(type='map', features=['река', 'мост'], summary='План лагеря у реки.'),
            40: dict(type='map', features=['лес', 'тропа'], summary='План чащи с тропой.')}[page]


def test_the_review_screen_marks_up_the_module_and_the_table_finds_it(tmp_path):
    with app(tmp_path, answers) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        # The picture reader is paid and slow, so the batch stays bounded: two
        # maps of the module, not every illustration of the book.
        jobs = client.app.state.adventure_jobs
        jobs.render = lambda id, width: picture(tmp_path, id)
        client.post('/api/adventure/review/session', json=dict(confirm_spoilers=True))

        batch = client.post('/api/adventure/review/library/' + bundle + '/markup',
                            json=dict(limit=2, request_id='r190-markup'))
        assert batch.status_code == 200, batch.text
        assert sorted(batch.json()['built']) == ['map-camp', 'map-wood'], batch.json()
        assert batch.json()['marked'] == 2 and batch.json()['left'] == 0, batch.json()
        status = client.get('/api/adventure/review/library/' + bundle + '/markup').json()
        assert status['total'] == 2 and status['marked'] == 2 and status['failed'] == 0, status

        # A person confirms both maps in the review screen: the description is
        # what the model saw, the name is what the person wrote.
        for id, name in (('map-camp', 'Карта лагеря'), ('map-wood', 'План чащи')):
            saved = client.put('/api/adventure/review/materials/' + id,
                               json=dict(scope='player', name=name, confirmed=True, sanitized=True))
            assert saved.status_code == 200, saved.text
        id = campaign(client, bundle)

        answer = turn(client, id, 'Показываю игрокам ту карту, где река').json()
        assert answer['status'] == 'resolved', answer
        assert answer['narrative'] == '«Карта лагеря» на столе, стр. 19.', answer['narrative']
        state = client.app.state.store.campaign(id)['state']['adventure']
        assert state['known_media'] == ['map-camp']
        # Two maps share nothing: a description of the wood finds the other one.
        answer = turn(client, id, 'Показываю игрокам карту, где лес').json()
        assert answer['status'] == 'resolved', answer
        assert 'План чащи' in answer['narrative'], answer['narrative']
        # And a description of nothing lists what the module does hold.
        answer = turn(client, id, 'Показываю карту, где вулкан').json()
        assert answer['status'] == 'unsupported', answer
        assert '«Карта лагеря» (стр. 19)' in answer['narrative'], answer['narrative']


def picture(tmp_path, id):
    from PIL import Image
    path = tmp_path / (id + '.png')
    if not path.is_file():
        Image.new('RGB', (64, 64), (40, 90, 60)).save(path)
    return path
