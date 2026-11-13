"""A picture is named by a printed line, not by a fragment of the prose beside it.

The review screen offers every map and handout of a book as a candidate, and a
person has to recognise the one they want from its name. A line cut out of the
middle of a sentence («ющие обозначения на карте показывают…»), a running head
(«Эпизод 1: Гринест в огне») and an empty string tell a person nothing, so the
name is taken from a whole printed line that is set apart from the body, and a
picture whose page prints no such line is left unnamed instead of misnamed.
"""
import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.module_media import candidates, repeated_lines

MAP = (30, 60, 90)
ILLUSTRATION = (120, 30, 60)


class Page:
    """One laid-out page: its printed text and the rectangles it was printed in."""

    def __init__(self, width=600.0, height=800.0):
        self.text = ''
        self.regions = []
        self.images = []
        self.width = width
        self.height = height

    def line(self, value, box):
        start = len(self.text)
        self.text += value + '\n'
        self.regions.append(dict(start=start, end=start + len(value), bounds=[list(box)]))
        return self

    def picture(self, name, box):
        self.images.append(dict(id=name, bounds=list(box)))
        return self

    def layout(self):
        return dict(version=1, coordinates='pdf_points_bottom_left', width=self.width,
                    height=self.height, images=self.images, regions=self.regions, vectors=[])


def only(page, **kwargs):
    found = candidates(page.layout(), page.text, **kwargs)
    assert len(found) == 1, found
    return found[0]


def test_a_heading_beside_the_box_wins_over_the_prose_below():
    page = Page().picture('p1.i0', (100, 300, 500, 700))
    page.line('Лагерь налётчиков', (150, 715, 350, 730))
    page.line('ющие обозначения на карте показывают расположение групп хижин', (60, 288, 540, 296))
    page.line('и стоящие в устье ложбины хижины охраняют вход в лагерь', (60, 276, 540, 284))
    found = only(page)
    assert found['caption'] == 'Лагерь налётчиков'


def test_the_running_head_of_the_chapter_is_never_a_name():
    page = Page().picture('p1.i0', (100, 300, 500, 700))
    # Deliberately not a word like «Эпизод»: such a line is refused as a chapter
    # heading anyway, and this test has to hold the repeat rule by itself.
    page.line('Greenest in flames', (60, 715, 360, 730))
    page.line('Greenest in flames', (60, 285, 360, 300))
    # The same line is printed on the chapter's other pages, so the book counts it
    # as a running head and it cannot name the picture below it.
    repeated = repeated_lines([page.text, page.text])
    found = only(page, repeated=repeated)
    # A picture the book does not name is named by its kind and page (r161): the
    # book's silence is not a reason to leave an empty card in review.
    assert found['caption'] == 'Иллюстрация · стр. 1'
    assert found['kind'] == 'illustration'
    # Without the repeat it is the nearest whole line and would be the name: the
    # rule above is what refuses it, not another filter.
    assert only(page)['caption'] == 'Greenest in flames'


def test_a_running_head_stops_the_search_instead_of_lending_a_deeper_title():
    page = Page().picture('p1.i0', (100, 300, 500, 700))
    page.line('Greenest in flames', (60, 715, 360, 730))
    page.line('The camp of the raiders', (60, 260, 360, 275))
    repeated = repeated_lines([page.text, page.text])
    assert only(page, repeated=repeated)['caption'] == 'Иллюстрация · стр. 1'


def test_a_line_that_repeats_elsewhere_is_not_a_name_even_without_a_section_word():
    page = Page().picture('p1.i0', (100, 300, 500, 700))
    page.line('Сокровища', (60, 715, 200, 730))
    found = only(page, repeated={'сокровища'})
    assert found['caption'] == 'Иллюстрация · стр. 1'


def test_a_sentence_that_begins_with_the_word_for_a_map_is_not_a_caption():
    page = Page().picture('p1.i0', (100, 300, 500, 700))
    page.line('Карте лагеря не хватает условных обозначений', (60, 288, 540, 296))
    page.line('и стоящие в устье ложбины хижины охраняют вход', (60, 276, 540, 284))
    found = only(page)
    # The page is a map because it says so, but the sentence is not its name.
    assert found['kind'] == 'map'
    assert found['caption'] == 'Карта · стр. 1'


def test_a_numbered_caption_keeps_its_full_stop():
    page = Page().picture('p1.i0', (100, 300, 500, 700))
    page.line('Карта 3. Лагерь налётчиков', (60, 715, 400, 730))
    assert only(page)['caption'] == 'Карта 3. Лагерь налётчиков'


def test_a_drawn_map_of_a_page_of_prose_is_named_by_its_kind_alone():
    page = Page()
    page.line('ющие обозначения на карте показывают расположение групп хижин', (60, 288, 540, 296))
    page.line('и стоящие в устье ложбины хижины охраняют вход', (60, 276, 540, 284))
    layout = page.layout()
    layout['vectors'] = [dict(id='p1.g%d' % index, bounds=[100, 100, 500, 700]) for index in range(5)]
    found = candidates(layout, page.text)
    assert len(found) == 1
    # The drawing is a map, and the prose that mentions the map is not its name.
    assert (found[0]['kind'], found[0]['caption']) == ('map', 'Карта · стр. 1')


def test_a_soft_break_is_glued_and_the_caption_is_read_whole():
    page = Page().picture('p1.i0', (100, 300, 500, 700))
    page.line('Карта 2-1: Логово Арау\ufffeтатора', (60, 715, 400, 730))
    found = only(page)
    assert found['caption'] == 'Карта 2-1: Логово Араутатора'
    assert found['kind'] == 'map'


def test_no_caption_leaves_the_name_empty_instead_of_a_fragment_of_prose():
    page = Page().picture('p1.i0', (100, 300, 500, 700))
    # The page continues its prose under the picture: the first line is cut off
    # mid-sentence by the column and the second is a whole line of the paragraph.
    page.line('ющие обозначения показывают расположение групп хижин', (60, 288, 540, 296))
    page.line('и стоящие в устье ложбины хижины охраняют вход в лагерь', (60, 276, 540, 284))
    page.line('Пленные культисты держат пленных в зоне', (60, 264, 540, 272))
    found = only(page)
    assert found['kind'] == 'illustration'
    assert found['caption'] == 'Иллюстрация · стр. 1'


def test_a_prose_line_cut_by_the_column_is_not_taken_for_a_title():
    page = Page().picture('p1.i0', (100, 400, 500, 700))
    # A whole line of a paragraph starts with a capital and ends without a full
    # stop; only its neighbour at ordinary leading tells it from a title.
    page.line('Интерьер хижин жаболюдов является полной', (60, 388, 540, 396))
    page.line('противоположностью общим домам людоящеров', (60, 376, 540, 384))
    found = only(page)
    assert found['caption'] == 'Иллюстрация · стр. 1'


def test_a_map_without_a_caption_is_named_by_its_kind():
    page = Page().picture('p1.i0', (100, 300, 500, 700))
    page.line('ющие обозначения на карте показывают расположение групп хижин', (60, 288, 540, 296))
    page.line('и стоящие в устье ложбины хижины охраняют вход в лагерь', (60, 276, 540, 284))
    found = only(page)
    assert found['kind'] == 'map'
    assert found['caption'] == 'Карта · стр. 1'


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
    """Four printed pages: a titled map, two pages under one running head, and a
    page whose only text beside the picture is a line of its prose."""
    writer = PdfWriter()
    _page(writer, lines=[(120, 730, 'The camp of the raiders'),
                         (60, 288, 'the palisade around the camp is watched all night')],
          pictures=[((100, 300, 500, 700), MAP)])
    for _ in range(2):
        _page(writer, lines=[(60, 740, 'Greenest in flames and the raid begins'),
                             (60, 288, 'the road below the ridge is watched all night')],
              pictures=[((100, 300, 500, 700), ILLUSTRATION)])
    _page(writer, lines=[(60, 288, 'the toll of the caravan is counted at the gate'),
                         (60, 276, 'and the patrol walks the road until dusk')],
          pictures=[((100, 300, 500, 700), ILLUSTRATION)])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_the_importer_names_a_picture_by_its_title_and_never_by_a_running_head(tmp_path):
    library = AdventureLibrary(tmp_path)
    bundle = library.create('r160 book')['id']
    path = tmp_path / 'book.pdf'
    path.write_bytes(book_pdf())
    run(tmp_path, library.enqueue(bundle, path, 'book.pdf', 'book')['id'])
    rows = {row['page']: row for row in library.review(bundle)['materials']
            if row['status'] == 'candidate'}
    assert rows[1]['caption'] == 'The camp of the raiders'
    # The chapter's head repeats on the pages that follow it, and the prose under
    # the picture is a paragraph: neither is its name.
    assert rows[2]['caption'] == 'Иллюстрация · стр. 2'
    assert rows[3]['caption'] == 'Иллюстрация · стр. 3'
    assert rows[4]['caption'] == 'Иллюстрация · стр. 4'
