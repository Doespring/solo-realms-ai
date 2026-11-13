"""A material is found by what the player says about it, not only by its name.

r155 drew the boundary honestly: the phrase at the table had to carry the
printed name or the page, so the Master speaking like a person («показываю ту
карту, где река», «раздаю письмо про Даффита») was answered with «такой карты
нет». r168 adds the second path — the words the book prints about the material —
and keeps every rule of the first: only a material a person confirmed and
sanitised may be laid out, a candidate is never revealed, and a description that
fits more than one material becomes a question instead of a guess.

The search reads the printed name, the caption and the text a person marked
public. It never reads the extracted page text: that stays behind the review
screen, and a search reading it would answer with words the table was never
allowed to see.
"""
import hashlib
import uuid

from fastapi.testclient import TestClient

from solo.adventure_engine import AdventureService
from solo.adventure_handout import asked, content_of, described, kinds_of, resolve
from solo.app import create_app
from solo.preview_mock import PreviewMock

DOCUMENT = hashlib.sha256(b'r168-free-description').hexdigest()


def material(id, page, *, kind='map', name='', caption='', text='', status='confirmed',
             scope='player', reviewed=1, sanitized=1):
    """One row the importer leaves behind, with the fields a person filled in."""
    return dict(id=id, document=DOCUMENT, page=page, kind=kind, scope=scope, status=status,
                public_name=name, caption=caption, public_text=text,
                reviewed=reviewed, sanitized=sanitized)


MAP = material('map-camp', 19, name='Карта лагеря',
               text='Лагерь налетчиков: с юга река, с севера частокол.')
CAVE = material('map-cave', 40, name='План пещеры', text='Пещера за водопадом, вход один.')
LETTER = material('letter-duffit', 21, kind='handout', name='Письмо Даффита',
                  text='Даффит просит долг вернуть до новолуния.')
CANDIDATE = material('cand-river', 23, kind='handout', name='', caption='Письмо барона Даффита о долге',
                     status='candidate', reviewed=0, sanitized=0)
# The phrase that reaches the candidate by description only: «с печатью» is
# printed by no material, so the printed-name path cannot claim it.
CANDIDATE_PHRASE = 'Раздаю письмо про Даффита и долг с печатью'


def test_a_description_without_the_name_finds_the_confirmed_material():
    # «Ту карту, где река» — the only describing word is «река», and the book
    # prints it about one material, so the material is found by description.
    found = resolve([MAP, CAVE, LETTER], **asked('Показываю игрокам ту карту, где река'))
    assert found['status'] == 'found', found
    assert found['material']['id'] == 'map-camp'


def test_two_words_of_the_description_are_enough():
    # «водопад» and «вход» are printed about one material each; both land in it.
    found = resolve([MAP, CAVE, LETTER], **asked('Показываю карту, вход у водопада'))
    assert found['status'] == 'found', found
    assert found['material']['id'] == 'map-cave'


def test_the_kind_word_of_the_phrase_narrows_the_search():
    # A map in the campaign also carries «Даффит»: only the kind word keeps the
    # handout from being confused with it.
    map_named = material('map-duffit', 11, name='Карта дома Даффита', text='Дом Даффита.')
    materials = [map_named, LETTER]
    assert kinds_of(asked('Раздаю письмо про Даффита')['key']) == ('handout',)
    # The act and the noun are not the same word: «раздатку» is the material.
    assert kinds_of(asked('Раздаю раздатку, где Даффит')['key']) == ('handout',)
    found = resolve(materials, **asked('Раздаю письмо про Даффита'))
    assert found['status'] == 'found', found
    assert found['material']['id'] == 'letter-duffit'
    # The same words without a kind word fit both materials: a question, not a
    # guess that would put the wrong card on the table.
    assert resolve(materials, **asked('Показываю лист, где Даффит'))['status'] == 'ambiguous'


def test_a_description_fitting_two_materials_is_one_question_not_a_choice():
    ford = material('map-ford', 44, name='План брода', text='Брод через реку у лагеря.')
    ambiguous = resolve([MAP, ford, LETTER], **asked('Показываю игрокам ту карту, где река'))
    assert ambiguous['status'] == 'ambiguous', ambiguous
    assert {row['id'] for row in ambiguous['materials']} == {'map-camp', 'map-ford'}
    assert 'Какой именно' in ambiguous['text']
    assert '«Карта лагеря» (стр. 19)' in ambiguous['text'] and '«План брода» (стр. 44)' in ambiguous['text']


def test_the_question_lists_three_materials_and_counts_the_rest():
    # Four maps fit «река, брод и водопад»: three are listed, the fourth counted.
    many = [material('m1', 51, name='Карта 1', text='Река и брод.'),
            material('m2', 52, name='Карта 2', text='Река и брод.'),
            material('m3', 53, name='Карта 3', text='Река и водопад.'),
            material('m4', 54, name='Карта 4', text='Река и водопад.')]
    crowded = resolve(many, **asked('Показываю карту, где река, брод и водопад'))
    assert crowded['status'] == 'ambiguous', crowded
    assert len(crowded['materials']) == 4
    assert crowded['text'].count('(стр. ') == 3 and 'и ещё 1' in crowded['text']


def test_a_description_of_a_candidate_reveals_nothing():
    # Nothing confirmed carries these words; the row nobody reviewed waits on
    # its page, and its caption — the thing the review screen exists to hide —
    # never reaches the table.
    waiting = resolve([MAP, CAVE, CANDIDATE], **asked(CANDIDATE_PHRASE))
    assert waiting['status'] == 'pending', waiting
    assert waiting['material']['id'] == 'cand-river'
    assert 'ждёт подтверждения' in waiting['text'] and 'стр. 23' in waiting['text']
    assert 'Даффит' not in waiting['text'] and 'барон' not in waiting['text'].lower()


def test_a_description_of_nothing_says_what_the_campaign_has():
    missing = resolve([MAP, CAVE], **asked('Показываю игрокам карту, где дракон'))
    assert missing['status'] == 'missing', missing
    assert '«Карта лагеря» (стр. 19)' in missing['text'] and '«План пещеры» (стр. 40)' in missing['text']


def test_a_word_the_book_prints_about_many_materials_describes_none_of_them():
    # Four maps share «комната»: such a word names a class of materials, so the
    # phrase is refused instead of one of them being picked.
    rooms = [material('room-%d' % n, 60 + n, name='Карта подвала %d' % n, text='Комната в подвале.')
             for n in range(4)]
    assert described(rooms, ['комнат'], rooms) == ([], 0)
    assert resolve(rooms, **asked('Показываю карту, где комната'))['status'] == 'missing'


def test_the_named_page_still_settles_the_description():
    ford = material('map-ford', 44, name='План брода', text='Брод через реку у лагеря.')
    found = resolve([MAP, ford], **asked('Показываю игрокам карту, где река, со стр. 44'))
    assert found['status'] == 'found', found
    assert found['material']['id'] == 'map-ford'


def test_the_description_path_is_only_for_phrases_that_carry_a_description():
    # A bare noun stays a request the host refuses: it is not a description.
    assert content_of(asked('Показываю игрокам карту')['key']) == []
    assert resolve([MAP], **asked('Показываю игрокам карту'))['status'] == 'missing'


def test_a_negated_description_lays_nothing_out():
    # «Карта, где НЕТ реки» describes the opposite of MAP: matching the word
    # alone would put that very map on the table, which is worse than refusing.
    for phrase in ('Показываю карту, где нет реки', 'Показываю карту без реки',
                   'Показываю карту, где не река'):
        refused = resolve([MAP, CAVE], **asked(phrase))
        assert refused['status'] == 'missing', (phrase, refused)
        assert 'отрицанием' in refused['text'], phrase


def test_words_that_describe_nothing_carry_no_weight():
    # «есть», «нужно», «только» are printed by the material but describe it not
    # at all: they are dropped before the match instead of scoring for it.
    assert content_of(asked('Показываю карту, где есть река')['key']) == ['рек']
    assert content_of(asked('Показываю карту, где только река')['key']) == ['рек']


def test_the_verb_forms_of_handing_material_out_are_recognised():
    # r155 matched «разда<что угодно>», which also ate the noun «раздатку»;
    # r168 narrowed the verb — and must not lose the everyday forms with it.
    for phrase in ('Раздавай письмо про долга', 'Не раздавай письмо про долга',
                   'Раздавал письмо про долга', 'Раздавать письмо про долга',
                   'Отдавал карту лагеря', 'Отдавай карту лагеря'):
        laid = asked(phrase)
        assert laid is not None, phrase
        assert {'письм', 'карт'} & set(laid['key']), (phrase, laid)
    assert kinds_of(asked('Раздаю раздатку, где Даффит')['key']) == ('handout',)


def test_a_pointer_word_is_still_part_of_a_printed_name():
    # «План один» is a name, not a description: the words that describe nothing
    # are dropped from the description match only, never from the printed name.
    one = material('plan-one', 5, name='План один')
    assert resolve([one], **asked('Показываю план один'))['status'] == 'found'


# --- the same path through the host, not only through resolve() ---------------


def app(tmp_path):
    return TestClient(create_app(tmp_path / 'solo.sqlite3', PreviewMock(), experimental=True, offline_mock=True))


def book(library):
    """The book r155 leaves behind, with the text a person marked public."""
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
        for row in (MAP, CAVE, CANDIDATE):
            db.execute('INSERT INTO materials(id,document,page,region,kind,scope,public_name,reviewed,'
                       'sanitized,status,caption,public_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (row['id'], DOCUMENT, row['page'], row['id'], row['kind'], row['scope'],
                        row['public_name'], row['reviewed'], row['sanitized'], row['status'],
                        row['caption'], row['public_text']))
    return bundle


def definition():
    return dict(id='r168', title='Лагерь налетчиков', ruleset='dnd2024', ruleset_version=1,
                source_ranges=[dict(document=DOCUMENT, first_page=1, last_page=40)])


def campaign(client, bundle):
    created = client.post('/api/prototype/campaigns', json=dict(
        name='Лира', request_id='r168-' + uuid.uuid4().hex[:8],
        build=client.get('/api/prototype/options').json()['default_build'],
        adventure='sandbox', encounter_monsters=[]))
    assert created.status_code == 200, created.text
    id = created.json()['id']
    AdventureService(client.app.state.store, client.app.state.adventures).attach(id, bundle, 'r168-attach', True)
    return id


def turn(client, id, text):
    version = client.get('/api/campaigns/' + id).json()['version']
    return client.post('/api/campaigns/' + id + '/command-turn', json=dict(
        request_id='r168-turn-' + uuid.uuid4().hex[:8], text=text, expected_version=version))


def test_the_description_at_the_table_lands_in_known_media(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        id = campaign(client, bundle)

        answer = turn(client, id, 'Показываю игрокам ту карту, где река').json()
        assert answer['status'] == 'resolved', answer
        assert answer['narrative'] == '«Карта лагеря» на столе, стр. 19.', answer['narrative']
        state = client.app.state.store.campaign(id)['state']['adventure']
        assert state['known_media'] == ['map-camp']
        assert state['journal'][-1]['effects'] == [{'kind': 'handout', 'target': 'map-camp'}]


def test_a_candidate_described_at_the_table_is_not_revealed_by_the_host(tmp_path):
    with app(tmp_path) as client:
        library = client.app.state.adventures
        bundle = book(library)
        library.save_definition(bundle, definition(), True)
        id = campaign(client, bundle)

        answer = turn(client, id, CANDIDATE_PHRASE).json()
        assert answer['status'] == 'unsupported', answer
        assert 'ждёт подтверждения' in answer['narrative'], answer['narrative']
        assert 'барон' not in answer['narrative'].lower(), answer['narrative']
        state = client.app.state.store.campaign(id)['state']['adventure']
        assert state['known_media'] == [] and 'cand-river' not in state['media_records']
