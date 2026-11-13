"""A place is named by the phrase the book prints, not by its tail.

r150 read the word after a kind noun as the name: «Город Чудес» became a city
called «Чудес», and «Болото Мертвецов» was no place at all, because «болото» was
not a kind the reader knew. Both are fragments of a name the book prints whole.

These tests pin the reading on the books' own sentences: the printed phrase is
the name when the word after the kind cannot stand on its own («Чудес»,
«Драконов», «Мертвецов», «Неронвейна» are genitives), the bare word stays a name
when the book prints it in the nominative («Гринест»), and a heading or a map
caption on the next printed line is not part of the name above it.

The pages are copied out of the extracted books («Клад Королевы Драконов» and
«Восстание Тиамат», RU), line breaks and the extractor's soft breaks included;
nothing in the fixtures was written for the test.
"""
import hashlib

from solo.adventure_library import AdventureLibrary
from solo.book_scene import module_scene, places
from solo.prepared_world import name_in_quote, normalized, source_span

DOCUMENT = hashlib.sha256(b'book-place-names-fixture').hexdigest()

#: «Болото Мертвецов» is named on one page and repeated on the next; the town of
#: «Город Чудес» is named once and referred to by its tail in the temple above it.
HOARD = {
    28: 'Эпизод 3: Драконья кладка\r\n'
        'Тиамат, а вулкан - это Колодец Драконов, через \r\n'
        'который Культ собирается привести Тиамат в мир. \r\n'
        'Колодец Драконов расположен на крайнем севере \r\n'
        'Закатных Гор.',
    36: 'Эпизод 4: В дороге\r\n'
        '• Высокий Дом Чудес - храм Гонда в северной части города',
    41: 'Эпизод 4: В дороге\r\n'
        'он направляется вновь на север – в \r\n'
        'Город Чудес, чтобы продолжить кутёж уже там.',
    49: 'Эпизод 5: Придорожное гостеприимство\r\n'
        'Холодное прибрежное болото, \r\n'
        'называемое Болото Мертвецов, лежит между доро\ufffeгой и берегом.',
    50: 'Эпизод 5: Придорожное гостеприимство\r\n'
        'Болото Мертвецов дикое и опасное место.',
    8: 'Эпизод 1: Гринест в огне\r\n'
       '6\r\n'
       'Городок Гринест [Greenest] был основан хал\ufffeфлингом Дхарвой Скаттерхарт, авантюристкой, \r\n'
       'но городок разросся в процветающее сообщество. Что не удивительно, так как Гринест - \r\n'
       'это единственное значительное поселение на Тропе Ульдуна. Золото караванов, проходящих \r\n'
       'через Гринест, приносит доходы городским мастерам.',
}

#: A heading stands alone on its line and the sentence under it is a new one; a
#: map title is a line of its own above the legend that describes it.
TIAMAT = {
    71: 'Эпизод 7: Башня Ксонтала\r\n'
        'Подземелье\r\n'
        'Хотя культисты заняли верхние уровни Башни Ксонта\ufffeла, они не контролируют её подземелья.',
    81: 'Эпизод 9: Возвращение Тиамат\r\n'
        'Храм Тиамат\r\n'
        'Карта кратера показывает, где появится Храм Тиамат, \r\n'
        'когда его поднимет магия Красных Волшебников.',
}


def read(tmp_path, pages, title=''):
    """The scene a chapter printed on these pages yields."""
    library = AdventureLibrary(tmp_path)
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status,pages) VALUES(?,?,?,?,?)',
                   (DOCUMENT, '.pdf', 1, 'extracted', max(pages)))
        for number, text in pages.items():
            db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',
                       (DOCUMENT, number, str(number), text))
    return module_scene(library, DOCUMENT, min(pages), max(pages), title=title)


def names(scene):
    return [place['name'] for place in scene['locations']]


def test_a_city_keeps_the_noun_that_makes_it_one(tmp_path):
    """«Город Чудес» is the name; «Чудес» is its genitive, not a city called Wonder."""
    scene = read(tmp_path, {36: HOARD[36], 41: HOARD[41]}, title='Эпизод 4: В дороге')
    assert names(scene) == ['Город Чудес']
    place = scene['locations'][0]
    assert (place['kind'], place['binding']) == ('city', 'printed')
    # The quote is the page's own sentence and the name is inside it, so the
    # place can still be opened on the page that printed it.
    assert source_span(HOARD[41], place['quote']) and place['page'] == 41
    assert name_in_quote(place['name'], normalized(place['quote']))


def test_a_swamp_named_after_the_dead_is_one_name(tmp_path):
    """«Болото Мертвецов» was invisible while «болото» was not a kind noun."""
    scene = read(tmp_path, {49: HOARD[49], 50: HOARD[50]}, title='Эпизод 5: Придорожное гостеприимство')
    assert names(scene) == ['Болото Мертвецов']
    assert scene['locations'][0]['kind'] == 'swamp'


def test_a_well_and_its_owner_are_read_as_one_name(tmp_path):
    """«Колодец Драконов» is printed twice on one page and is the whole place."""
    scene = read(tmp_path, {28: HOARD[28]}, title='Эпизод 3: Драконья кладка')
    assert names(scene) == ['Колодец Драконов']
    assert scene['locations'][0]['kind'] == 'well'


def test_a_name_the_book_prints_on_its_own_stays_bare(tmp_path):
    """«Гринест» is printed after «через», so the kind noun before it describes it."""
    scene = read(tmp_path, {8: HOARD[8]}, title='Эпизод 1: Гринест в огне')
    assert names(scene) == ['Гринест']
    assert scene['locations'][0]['kind'] == 'town'


def test_a_heading_is_not_the_name_of_the_sentence_below_it():
    """«Подземелье / Хотя культисты заняли…» is a heading and a new sentence."""
    assert places(TIAMAT[71]) == []


def test_a_caption_on_the_next_line_does_not_extend_a_name():
    """«Храм Тиамат / Карта кратера…» is a title and its legend, not «Тиамат Карта»."""
    found = places(TIAMAT[81])
    # The temple is named twice on the page — in the title and in its legend — and
    # neither reading may take the first word of the next line into the name.
    assert {item['name'] for item in found} == {'Тиамат'}
    assert {item['tail'] for item in found} == {''}

