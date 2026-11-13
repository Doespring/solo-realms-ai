"""A material answers with its own text, not with the page it was cut from.

A handout or a map is printed in its own box on a page, and the page around it
belongs to the neighbours: prose, a running head, another column. The review
screen asks the material what it says, so the box decides which lines are its
own, and the lines are read by columns and paragraphs — the extractor glues the
left column of a two-column page to the right one, and that glue must not travel
with the material.
"""
import io

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.app import create_app

LEFT = (60, 720)
RIGHT = (330, 720)
LETTER = (120, 650)


def _picture(writer, xobjects, box, colour):
    left, bottom, right, top = box
    width, height = int(right - left), int(top - bottom)
    stream = DecodedStreamObject()
    stream.set_data(bytes(colour) * (width * height))
    stream.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                   NameObject('/Width'): NumberObject(width), NameObject('/Height'): NumberObject(height),
                   NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
                   NameObject('/BitsPerComponent'): NumberObject(8)})
    name = '/Im%d' % (len(xobjects) + 1)
    xobjects[NameObject(name)] = writer._add_object(stream)
    return 'q %d 0 0 %d %s %s cm %s Do Q' % (width, height, left, bottom, name)


def _page(writer, lines=(), pictures=()):
    page = writer.add_blank_page(600, 800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    fonts = DictionaryObject({NameObject('/F1'): writer._add_object(font)})
    xobjects = DictionaryObject()
    commands = [_picture(writer, xobjects, box, colour) for box, colour in pictures]
    for x, y, text in lines:
        escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        commands.append('BT /F1 9 Tf %s %s Td (%s) Tj ET' % (x, y, escaped))
    content = DecodedStreamObject()
    content.set_data(' '.join(commands).encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(content)
    page[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/Font'): fonts, NameObject('/XObject'): xobjects})


def book_pdf():
    """Four printed pages: a two-column page with a box over the left column, a
    picture on a page without text, a letter printed inside its own box, and a
    picture printed in the middle of a page of prose."""
    writer = PdfWriter()
    left = ['Left column first line of the camp', 'Left column second line of the road',
            'Left column third line of the river', 'Left column fourth line of the inn']
    right = ['Right column first line of the letter', 'Right column second line of the seal',
             'Right column third line of the elder', 'Right column fourth line of the keep']
    column = []
    for index, (first, second) in enumerate(zip(left, right)):
        column.append((LEFT[0], LEFT[1] - 13 * index, first))
        column.append((RIGHT[0], RIGHT[1] - 13 * index, second))
    _page(writer, lines=column, pictures=[((50, 300, 290, 745), (30, 60, 90))])
    _page(writer, pictures=[((200, 300, 500, 600), (120, 30, 60))])
    letter = ['The sealed letter begins here and speaks', 'of the estate with its old garden and',
              'the promise made by the younger brother to', 'the elder one who kept the gate closed.']
    lines = [(LETTER[0], LETTER[1] - 11.5 * index, text) for index, text in enumerate(letter)]
    lines.append((110, 170, 'Handout: the sealed letter'))
    _page(writer, lines=lines, pictures=[((100, 200, 500, 700), (200, 180, 120))])
    # A page of prose with a picture printed in the middle of it: the box holds no
    # lines, and the prose around it must not come with the picture.
    _page(writer, lines=[(60, 760, 'The road climbs to the pass and the patrols walk it.'),
                         (60, 745, 'The camp below the ridge is quiet at noon.')],
          pictures=[((200, 300, 500, 600), (30, 60, 90))])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def imported(tmp_path):
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Раздатки и карты')['id']
    path = tmp_path / 'book.pdf'
    path.write_bytes(book_pdf())
    run(tmp_path, library.enqueue(bundle, path, 'book.pdf', 'book')['id'])
    return library, bundle


def material_of(library, bundle, page):
    return next(item for item in library.review(bundle)['materials']
                if item['page'] == page and item['bounds'])


def review_client(tmp_path):
    app = create_app(tmp_path / 'solo.sqlite3')
    library, bundle = imported(tmp_path)
    client = TestClient(app)
    assert client.post('/api/adventure/review/session',
                       json={'confirm_spoilers': True}).status_code == 200
    return client, library, bundle


def test_a_two_column_page_gives_the_box_only_its_own_column(tmp_path):
    client, library, bundle = review_client(tmp_path)
    material = material_of(library, bundle, 1)
    answer = client.get('/api/adventure/review/materials/' + material['id'] + '/text').json()
    # The box covers the left column, so the left column is the material's text.
    assert answer['mode'] == 'box' and answer['lines'] == 4
    text = answer['text']
    order = [text.index(name) for name in ('first line of the camp', 'second line of the road',
                                           'third line of the river', 'fourth line of the inn')]
    assert order == sorted(order)
    # The right column is the neighbour's text: it was counted as dropped, and
    # not one of its words came with the material.
    assert answer['dropped'] >= 4
    assert 'Right column' not in text and 'letter' not in text and 'seal' not in text
    # The extractor glues the two columns into one string per printed line; that
    # glue is what used to put the right column in the middle of the left one.
    whole = client.get('/api/adventure/review/materials/' +
                       next(item['id'] for item in library.review(bundle)['materials']
                            if item['page'] == 1 and not item['bounds']) + '/text').json()
    assert 'Left column first line of the camp Right column' in whole['text']


def test_a_page_material_still_answers_with_its_whole_page(tmp_path):
    client, library, bundle = review_client(tmp_path)
    page = next(item for item in library.review(bundle)['materials']
                if item['page'] == 1 and not item['bounds'])
    answer = client.get('/api/adventure/review/materials/' + page['id'] + '/text').json()
    assert answer['mode'] == 'page' and answer['text'].count('column') == 8
    assert 'Left' in answer['text'] and 'Right' in answer['text']


def test_a_box_reads_its_lines_as_one_paragraph_and_leaves_the_caption_out(tmp_path):
    client, library, bundle = review_client(tmp_path)
    material = material_of(library, bundle, 3)
    answer = client.get('/api/adventure/review/materials/' + material['id'] + '/text').json()
    text = answer['text']
    # The printed lines of the letter flow as one paragraph; the caption printed
    # under the box is not part of it.
    assert '\n\n' not in text
    assert text.startswith('The sealed letter begins here') and text.endswith('kept the gate closed.')
    assert 'Handout:' not in text
    assert answer['lines'] == 4 and answer['dropped'] == 1


def test_a_picture_on_a_page_without_text_answers_empty_and_says_why(tmp_path):
    client, library, bundle = review_client(tmp_path)
    material = material_of(library, bundle, 2)
    answer = client.get('/api/adventure/review/materials/' + material['id'] + '/text').json()
    assert answer['text'] == '' and answer['mode'] == 'box' and answer['lines'] == 0
    # The page has no printable text at all, so the warning names that, not a
    # picture: "no lines" and "the lines have no geometry" are different defects.
    assert 'не нашлось строк' in answer['warning']


def test_a_picture_inside_a_page_of_prose_does_not_carry_the_prose(tmp_path):
    client, library, bundle = review_client(tmp_path)
    material = material_of(library, bundle, 4)
    answer = client.get('/api/adventure/review/materials/' + material['id'] + '/text').json()
    assert answer['text'] == '' and answer['mode'] == 'box'
    # The prose of the page was printed outside the box and is counted as such.
    assert answer['dropped'] == 2
    assert 'это рисунок' in answer['warning']

def test_a_box_that_cannot_be_read_answers_with_the_page_and_says_so(tmp_path):
    client, library, bundle = review_client(tmp_path)
    material = material_of(library, bundle, 1)
    page = next(item for item in library.review(bundle)['materials']
                if item['page'] == 1 and not item['bounds'])
    with library.db() as db:
        db.execute("UPDATE materials SET bounds='не коробка' WHERE id=?", (material['id'],))
        db.execute('DELETE FROM pdf_layout WHERE page=?', (material['page'],))
    # Broken bounds and no layout of the page are two different failures of the
    # same answer: the page is returned, and the warning names the reason instead
    # of pretending the box holds a picture.
    broken = client.get('/api/adventure/review/materials/' + material['id'] + '/text').json()
    whole = client.get('/api/adventure/review/materials/' + page['id'] + '/text').json()
    assert broken['mode'] == 'page' and broken['text'] == whole['text']
    assert 'Отдан текст страницы целиком' in broken['warning']
