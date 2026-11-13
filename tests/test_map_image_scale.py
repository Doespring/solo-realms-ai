"""A map is read by the model, so it must not arrive as a 384 px preview.

`solo/analysis_images.py` used to cut every picture to 384 px on the long side.
For a printed map that is unreadable: the room numbers the model is asked about
are a few pixels wide. A map is now rendered from its own printed box up to
MAP_SIDE and, when the provider's data: URL limit is tight, paid for with a
16-colour palette instead of resolution. Pages and illustrations keep the
384 px preview, and every request still carries at most two pictures.
"""
import base64
import io

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from solo.adventure_analysis import image_kind
from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.analysis_images import (MAP_SIDE, MAX_URL, PAGE_SIDE, image_count,
                                  map_materials, png_part, render_selected)

MAP = (30, 60, 90)
ILLUSTRATION = (120, 30, 60)


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


def book_pdf():
    """Two printed pages: a captioned map and a captioned illustration."""
    writer = PdfWriter()
    _page(writer, lines=[(60, 740, 'The camp of the raiders lies below the ridge.'),
                         (60, 270, 'Map: the camp of the raiders')],
          pictures=[((50, 300, 550, 700), MAP)])
    _page(writer, lines=[(110, 320, 'Portrait of a dragon at rest')],
          pictures=[((100, 350, 500, 650), ILLUSTRATION)])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def imported(tmp_path):
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Клад Королевы Драконов')['id']
    path = tmp_path / 'book.pdf'
    path.write_bytes(book_pdf())
    job = library.enqueue(bundle, path, 'book.pdf', 'book')
    run(tmp_path, job['id'])
    return library


def _material(library, kind):
    with library.db() as db:
        row = db.execute('SELECT document,page FROM materials WHERE kind=? ORDER BY page LIMIT 1', (kind,)).fetchone()
    assert row is not None
    return row['document'], row['page']


def _decoded(parts, index):
    raw = base64.b64decode(parts[index]['image_url']['url'].split(',', 1)[1])
    return Image.open(io.BytesIO(raw))


def test_a_map_reaches_the_model_larger_than_a_preview(tmp_path):
    library = imported(tmp_path)
    document, page = _material(library, 'map')
    parts = render_selected(library, document, [page], 'map')
    # The map is cut out of its printed box, so the model sees the map itself and
    # not a page with columns of text around it.
    assert map_materials(library, document, [page])[page]
    assert len(parts) == 2 and parts[0]['type'] == 'text' and 'карты' in parts[0]['text']
    image = _decoded(parts, 1)
    # A flat synthetic map fits the limit at full size; a real scan is bought
    # down (see the noisy test below), but never below the preview size.
    assert max(image.size) >= MAP_SIDE - 2, image.size
    assert max(image.size) > PAGE_SIDE, image.size
    assert len(parts[1]['image_url']['url']) <= MAX_URL
    assert parts[1]['image_url']['detail'] == 'high'
    assert image_count(parts) == 1


def test_a_page_and_an_illustration_stay_a_small_preview(tmp_path):
    library = imported(tmp_path)
    map_document, map_page = _material(library, 'map')
    illustration_document, illustration_page = _material(library, 'illustration')
    page_parts = render_selected(library, map_document, [map_page])
    illustration_parts = render_selected(library, illustration_document, [illustration_page])
    for parts in (page_parts, illustration_parts):
        image = _decoded(parts, 1)
        assert max(image.size) <= PAGE_SIDE, image.size
        assert parts[1]['image_url']['detail'] == 'low'
        assert len(parts[1]['image_url']['url']) <= MAX_URL


def test_a_map_without_a_cut_out_falls_back_to_the_page_but_still_larger(tmp_path):
    library = imported(tmp_path)
    document, page = _material(library, 'illustration')
    parts = render_selected(library, document, [page], 'map')
    image = _decoded(parts, 1)
    assert max(image.size) > PAGE_SIDE, image.size
    assert len(parts[1]['image_url']['url']) <= MAX_URL
    assert image_count(parts) == 1


def test_a_dense_map_is_bought_down_to_the_url_limit_before_sending():
    # A noisy scan compresses badly: the full-colour 1024 px render cannot fit the
    # provider's limit, so the palette and then the size are reduced - never the
    # other way round, and never past the limit.
    noisy = Image.effect_noise((MAP_SIDE, MAP_SIDE), 64).convert('RGB')
    part = png_part(noisy, MAP_SIDE)
    url = part['image_url']['url']
    assert len(url) <= MAX_URL, len(url)
    assert part['image_url']['detail'] == 'high'
    image = Image.open(io.BytesIO(base64.b64decode(url.split(',', 1)[1])))
    assert max(image.size) >= 64 and image.format == 'PNG'
    assert image_count([part]) == 1


def test_two_pictures_are_the_ceiling_and_a_big_one_needs_the_map_mark():
    small = png_part(Image.new('RGB', (320, 200), 'white'))
    assert small['image_url']['detail'] == 'low' and image_count([small]) == 1
    big = png_part(Image.new('RGB', (512, 512), 'white'), MAP_SIDE)
    assert image_count([big, big]) == 2
    with pytest.raises(ValueError):
        image_count([big, big, big])
    # A 512 px picture is accepted only as a map: the same bytes marked as an
    # ordinary preview would exceed the preview size.
    unmarked = dict(type='image_url', image_url=dict(url=big['image_url']['url'], detail='low'))
    with pytest.raises(ValueError):
        image_count([unmarked])


def test_the_map_kind_comes_from_the_imported_material(tmp_path):
    library = imported(tmp_path)
    document, page = _material(library, 'map')
    assert image_kind(library, document, [page]) == 'map'
    illustration_document, illustration_page = _material(library, 'illustration')
    assert image_kind(library, illustration_document, [illustration_page]) == 'page'
