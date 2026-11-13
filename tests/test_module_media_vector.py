"""A map a book draws with curves is a picture too, but it has no image object.

A typeset module prints its maps as vector paths: pdfium reports no picture on
such a page at all, so the page-level media step of r151 would lose the map. The
page must still say the word for a map, the drawing must be big enough to be one,
must not be the page itself, and must not be the frame of a column of text — a
map is not guessed from any large drawing, and prose about a «карта» lying on a
table is not a map.
"""
import io
import json

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import render_material, run
from solo.module_media import candidates

MAP = (30, 60, 90)

#: The rooms of a small drawn map: many path objects on one page, each a room.
ROOMS = [(60 + 60 * column, 310 + 60 * row, 100 + 60 * column, 345 + 60 * row)
         for column in range(8) for row in range(6)]
ROOM_BOX = (60, 310, 520, 645)


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


def _page(writer, lines=(), rooms=(), frame=None, pictures=()):
    """A page of a typeset book: printed lines and a drawing made of paths."""
    page = writer.add_blank_page(600, 800)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    fonts = DictionaryObject({NameObject('/F1'): writer._add_object(font)})
    xobjects = DictionaryObject()
    commands = [_picture(writer, xobjects, box, colour) for box, colour in pictures]
    strokes = []
    if frame:
        left, bottom, right, top = frame
        strokes.append('%g %g %g %g re S' % (left, bottom, right - left, top - bottom))
    for left, bottom, right, top in rooms:
        strokes.append('0.2 0.3 0.4 rg %g %g %g %g re f' % (left, bottom, right - left, top - bottom))
    if strokes:
        commands.append('0.4 0.4 0.4 RG ' + ' '.join(strokes))
    for x, y, text in lines:
        escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        commands.append('BT /F1 12 Tf %s %s Td (%s) Tj ET' % (x, y, escaped))
    content = DecodedStreamObject()
    content.set_data(' '.join(commands).encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(content)
    page[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/Font'): fonts, NameObject('/XObject'): xobjects})


def vector_book_pdf():
    """Six pages: a drawn map, a framed column of prose about a map, a picture
    beside a drawing, a drawing the size of the page, an ornament, and a drawing
    whose page prints no word for a map."""
    writer = PdfWriter()
    _page(writer, lines=[(60, 740, 'Map: the camp of the raiders')], rooms=ROOMS)
    # A framed column of words: the page prints «map», and its frame is big, but
    # the words are the content — the frame is not a picture.
    column = [(70, 660 - 26 * index, 'The map on the table shows the marching route of the horde.')
              for index in range(14)]
    _page(writer, lines=column, frame=(60, 300, 540, 720))
    # A printed picture wins over a drawing on the same page.
    _page(writer, lines=[(60, 740, 'Map: the camp of the raiders')],
          pictures=[((50, 120, 550, 500), MAP)], rooms=[room for room in ROOMS if room[1] > 550])
    # A drawing the size of the page is the page, not an asset.
    _page(writer, lines=[(60, 740, 'Map of the whole crater')], rooms=[(0, 0, 600, 800)])
    # An ornament is not a map, however the page names it.
    _page(writer, lines=[(60, 740, 'Map: a small device')], rooms=[(60, 400, 160, 500)])
    # A drawing without a printed word for a map is not called a map.
    _page(writer, lines=[(60, 740, 'The chamber is dark and cold.')], rooms=ROOMS)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def imported(tmp_path, purpose='book'):
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Пещера чертогов')['id']
    path = tmp_path / 'book.pdf'
    path.write_bytes(vector_book_pdf())
    job = library.enqueue(bundle, path, 'book.pdf', purpose)
    run(tmp_path, job['id'])
    return library, bundle


def candidates_of(library, bundle):
    return {material['page']: material for material in library.review(bundle)['materials']
            if material['status'] == 'candidate'}


def laid_out(tmp_path):
    """The layout and text of every page, as the importer read them."""
    import pypdfium2 as pdfium
    from solo.pdf_pipeline import layout_page
    path = tmp_path / 'book.pdf'
    path.write_bytes(vector_book_pdf())
    pages = []
    with pdfium.PdfDocument(str(path)) as pdf:
        for number in range(1, len(pdf) + 1):
            page = pdf[number - 1]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range() or ''
                pages.append((layout_page(page, textpage, text, number), text))
            finally:
                textpage.close()
                page.close()
    return pages


def test_a_drawn_map_is_laid_out_as_paths_on_its_page(tmp_path):
    layout, text = laid_out(tmp_path)[0]
    assert layout['vectors'], 'the drawing of page 1 left no path object'
    assert len(layout['vectors']) >= len(ROOMS)
    assert layout['images'] == [] and text.startswith('Map: the camp')
    # Every path is named after its page, so a material can point back at it.
    assert all(graphic['id'].startswith('p1.g') for graphic in layout['vectors'])


def test_a_drawn_map_becomes_a_map_candidate(tmp_path):
    library, bundle = imported(tmp_path)
    found = candidates_of(library, bundle)
    drawn = found[1]
    assert drawn['kind'] == 'map'
    # The region says where it came from: `v` is a vector drawing, not a picture.
    assert drawn['region'] == 'p1.v1'
    assert drawn['scope'] == 'gm' and drawn['reviewed'] == 0
    # Its box is the box of the drawing in PDF points, so the cut can be checked.
    assert [round(value) for value in json.loads(drawn['bounds'])] == [round(value) for value in ROOM_BOX]
    # The printed line is kept as the reason the page was believed.
    assert drawn['caption'].startswith('Map: the camp of the raiders')


def test_a_framed_column_of_text_is_not_a_map(tmp_path):
    library, bundle = imported(tmp_path)
    # Page 2 prints «map» and draws a large frame around its column; the words
    # cover the frame, so the frame is a text box and not a picture.
    assert 2 not in candidates_of(library, bundle)


def test_a_picture_is_never_replaced_by_a_drawing(tmp_path):
    library, bundle = imported(tmp_path)
    found = candidates_of(library, bundle)
    assert 3 in found and found[3]['kind'] == 'map'
    assert found[3]['region'] == 'p3.i0'
    assert [round(value) for value in json.loads(found[3]['bounds'])] == [50, 120, 550, 500]


def test_a_page_sized_drawing_and_an_ornament_are_not_candidates(tmp_path):
    library, bundle = imported(tmp_path)
    found = candidates_of(library, bundle)
    assert 4 not in found and 5 not in found


def test_a_drawing_on_a_page_without_the_word_is_not_a_map(tmp_path):
    library, bundle = imported(tmp_path)
    assert 6 not in candidates_of(library, bundle)


def test_the_drawing_is_refused_by_the_printed_text_it_carries():
    # The guard is pinned here without a PDF: the same box is a picture while the
    # text covers a tenth of it, and a text box once the words cover a quarter —
    # which is what the framed column of page 2 and the framed columns of the
    # Tiamat book measure.
    def page(cover):
        height = cover * 400.0
        return dict(width=600.0, height=800.0, images=[], hints=[],
                    vectors=[dict(id='p7.g0', bounds=[200.0, 200.0, 400.0, 600.0])],
                    regions=[dict(start=0, end=5, bounds=[[200.0, 400.0 - height / 2,
                                                          400.0, 400.0 + height / 2]])])
    assert candidates(page(0.1), 'Карта пещеры')[0]['region'] == 'p7.v1'
    assert candidates(page(0.25), 'Карта пещеры') == []
    assert candidates(page(0.0), 'Пещера темна и холодна') == []


def test_a_drawn_map_is_rendered_from_its_own_box(tmp_path):
    library, bundle = imported(tmp_path)
    material = candidates_of(library, bundle)[1]
    path = render_material(library, material['id'], 2048)
    with Image.open(path) as image:
        # 460x335 pt, so the map is 2048x1490 — not the whole 600x800 page.
        assert abs(image.width - 2048) <= 2
        assert abs(image.height - 2048 * 335 / 460) <= 3
        picture = image.convert('RGB')
        pixels = [picture.getpixel((x, y)) for x in range(0, 2048, 64) for y in range(0, 1200, 64)]
        assert min(sum(pixel) for pixel in pixels) < 400
        assert max(sum(pixel) for pixel in pixels) > 600
