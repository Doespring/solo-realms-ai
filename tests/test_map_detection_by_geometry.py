"""A page whose whole content is a drawing is a map, even unlabelled.

A scanned module prints its maps as a picture that is the page: the reader sees
a region map or a floor plan with a compass, room numbers and a scale drawn
*inside* the picture, so the page prints no word for it and r151 offered it as
an illustration. The geometry of the page already says what it is — the picture
covers most of the page and the page sets no prose beside it — and the caption
say which signals made the decision, so a person in review can dispute it.

A picture set into a page of prose is an illustration of that prose however large
it is, and a plate of art is printed smaller than a map: the share of the page is
what tells the two apart when the book printed no word.
"""
import io

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.module_media import candidates, map_evidence, service_name

MAP = (30, 60, 90)


class Page:
    """One laid-out page: its printed text and the rectangles it was printed in."""

    def __init__(self, width=600.0, height=800.0, page=1):
        self.text = ''
        self.regions = []
        self.images = []
        self.width = width
        self.height = height
        self.page = page

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
                    height=self.height, images=self.images, regions=self.regions, vectors=[],
                    render_ref='p%d.render' % self.page)


def only(page, **kwargs):
    found = candidates(page.layout(), page.text, page=page.page, **kwargs)
    assert len(found) == 1, found
    return found[0]


def test_a_page_that_is_its_picture_becomes_a_map_without_the_word():
    # The floor plan of the module: one picture edge to edge, a folio printed
    # below it, and nothing else — no prose of a text page anywhere.
    page = Page(page=87).picture('p87.i0', (20, 40, 575, 720))
    page.line('85', (290, 20, 300, 30))
    found = only(page)
    assert found['kind'] == 'map'
    # The decision is written down, not guessed: the caption names the kind, the
    # page, and the signals that made the picture a map.
    assert found['caption'].startswith('Карта · стр. 87')
    assert 'вся страница' in found['caption'] and 'прозы вокруг нет' in found['caption']


def test_the_geometry_of_the_real_page_is_what_makes_it_a_map():
    # The same page with the picture set small is a plate, not a map: this is
    # the whole difference the rule rests on, so it is checked on its own.
    page = Page(page=3).picture('p3.i0', (20, 40, 575, 460))
    page.line('КЛАД КОРОЛЕВЫ ДРАКОНОВ', (60, 720, 360, 740))
    evidence = map_evidence(page.layout(), page.text, (20, 40, 575, 460))
    assert evidence is None
    assert only(page)['kind'] == 'illustration'


def test_a_large_picture_set_into_a_page_of_prose_stays_an_illustration():
    # A full-page illustration of a scene: the page prints the body text beside
    # it, so the picture illustrates that text instead of being the page.
    page = Page(page=62).picture('p62.i0', (45, 446, 544, 816))
    for index in range(20):
        page.line('Стража замка держит ворота и считает повозки на дороге.',
                  (60, 400 - 18 * index, 540, 408 - 18 * index))
    found = only(page)
    assert found['kind'] == 'illustration'
    assert found['caption'] == 'Иллюстрация · стр. 62'


def test_keys_printed_beside_the_drawing_do_not_turn_prose_into_a_map():
    # Room numbers are printed as text on a typeset plan, and a page of prose
    # about a dungeon prints the same numbers: only a page without prose around
    # the drawing is a map.
    page = Page(page=62).picture('p62.i0', (45, 446, 544, 816))
    page.line('6. Главные ворота', (60, 400, 240, 412))
    page.line('Это единственный вход в замок, которым пользуются.', (60, 382, 540, 394))
    page.line('Ворота никогда не закрываются, их створки провисают на петлях.', (60, 364, 540, 376))
    assert only(page)['kind'] == 'illustration'


def test_a_drawn_plan_with_its_legend_and_no_prose_is_a_map():
    # A typeset plan: the drawing is smaller than the page but every line the
    # page prints belongs to the map (rooms, a scale), so it is still a map.
    page = Page(page=41).picture('p41.i0', (100, 380, 500, 700))
    page.line('1. Ворота', (60, 340, 200, 352))
    page.line('2. Зал стражи', (60, 322, 200, 334))
    page.line('3. Лестница', (60, 304, 200, 316))
    page.line('1 клетка = 5 футов', (60, 280, 240, 292))
    found = only(page)
    assert found['kind'] == 'map'
    assert 'номера комнат' in found['caption'] and 'масштаб/сетка' in found['caption']


def test_a_printed_word_for_a_map_still_names_it_and_needs_no_excuse():
    page = Page(page=32).picture('p32.i0', (100, 340, 500, 700))
    page.line('Карта 2-1: Логово Араутатора', (100, 710, 400, 725))
    found = only(page)
    assert (found['kind'], found['caption']) == ('map', 'Карта 2-1: Логово Араутатора')


def test_an_unnamed_picture_is_named_by_its_kind_and_page():
    assert service_name('illustration', 62) == 'Иллюстрация · стр. 62'
    assert service_name('map', 87) == 'Карта · стр. 87'
    # Without a known page the name still says what the picture is.
    assert service_name('handout', 0) == 'Раздатка'


def test_a_picture_just_below_the_page_share_stays_an_illustration():
    # 0.72 of the page: a big picture, but not the page. The threshold is the
    # only thing that keeps a full-page plate of art out of the maps, so it is
    # pinned from both sides instead of trusted.
    page = Page(page=4).picture('p4.i0', (40, 60, 580, 700))
    page.line('7', (290, 20, 300, 30))
    assert only(page)['kind'] == 'illustration'


def test_a_picture_just_above_the_page_share_is_a_map():
    # 0.77 of the page with no prose anywhere: the same page as a map.
    page = Page(page=6).picture('p6.i0', (20, 40, 580, 700))
    page.line('4', (290, 20, 300, 30))
    assert only(page)['kind'] == 'map'


def test_a_page_of_text_over_the_picture_is_not_a_map():
    # A framed column of words drawn as a picture, or a scan under a printed
    # page: the text covers the box, so the picture is the page, not a map.
    page = Page(page=5).picture('p5.i0', (20, 40, 580, 700))
    # The lines are set apart, so they are labels of a drawing rather than the
    # paragraphs of a body: only the share of the box they cover refuses them.
    for index in range(12):
        page.line('The caravan keeps to the road while the patrol counts carts',
                  (40, 200 + 30 * index, 560, 212 + 30 * index))
    assert only(page)['kind'] == 'illustration'


def test_prose_printed_inside_the_box_is_prose_too():
    # The text of a scan can sit under the picture as an invisible layer: the
    # page is a text page either way, and its picture is not a map.
    page = Page(page=8).picture('p8.i0', (20, 40, 580, 700))
    page.line('Стража замка держит ворота и считает повозки на дороге', (40, 300, 560, 312))
    page.line('Ворота никогда не закрываются, их створки провисают на петлях', (40, 286, 560, 298))
    page.line('Опускающаяся решётка подготовлена так, чтобы упасть вниз', (40, 272, 560, 284))
    assert only(page)['kind'] == 'illustration'


def test_the_books_own_handout_heading_beats_the_geometry():
    page = Page(page=9).picture('p9.i0', (20, 40, 580, 700))
    # The heading is printed far above the picture, so it is not its caption: the
    # kind comes from the page's own hint, and the hint must win over geometry.
    page.line('Раздаточный материал', (60, 760, 300, 772))
    layout = page.layout()
    layout['hints'] = [dict(kind='handout', region='p9.t0-19', status='requires_review')]
    found = candidates(layout, page.text, page=9)
    assert [item['kind'] for item in found] == ['handout']


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
        commands.append('BT /F1 12 Tf %s %s Td (%s) Tj ET' % (x, y, escaped))
    content = DecodedStreamObject()
    content.set_data(' '.join(commands).encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(content)
    page[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/Font'): fonts, NameObject('/XObject'): xobjects})


def scanned_book_pdf():
    """Three printed pages: an unlabelled map edge to edge, a plate of art, and
    a scene illustration set into a page of prose."""
    writer = PdfWriter()
    _page(writer, lines=[(290, 20, '5')], pictures=[((20, 40, 575, 720), MAP)])
    _page(writer, lines=[(60, 740, 'TREASURE OF THE DRAGON QUEEN')], pictures=[((20, 40, 575, 460), MAP)])
    prose = [(60, 400 - 18 * index, 'The caravan keeps to the road while the patrol counts its carts.')
             for index in range(18)]
    _page(writer, lines=prose, pictures=[((45, 446, 544, 816), MAP)])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_the_import_cuts_an_unlabelled_map_out_as_a_map(tmp_path):
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Клад Королевы Драконов')['id']
    path = tmp_path / 'book.pdf'
    path.write_bytes(scanned_book_pdf())
    run(tmp_path, library.enqueue(bundle, path, 'book.pdf', 'book')['id'])
    found = {item['page']: item for item in library.review(bundle)['materials']
             if item['status'] == 'candidate'}
    kinds = {page: item['kind'] for page, item in found.items()}
    assert kinds == {1: 'map', 2: 'illustration', 3: 'illustration'}
    assert found[1]['caption'].startswith('Карта · стр. 1')
    # The plate is printed smaller than a map, and the scene sits in prose: both
    # stay illustrations, and neither is an empty card in review.
    assert found[2]['caption'] == 'Иллюстрация · стр. 2'
    assert found[3]['caption'] == 'Иллюстрация · стр. 3'
    # The public name of a candidate is the same name without the review notes:
    # confirming starts from a name the review screen already showed, and a
    # player told the material is not confirmed yet is not read the signals.
    material = library.material(found[1]['id'])
    assert material['public_name'] == 'Карта · стр. 1'
    assert material['caption'].startswith('Карта · стр. 1 (')
