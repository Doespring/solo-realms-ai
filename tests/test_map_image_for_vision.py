"""The map a model gets is large enough to read, not a shrunken whole map.

`solo/analysis_images.py` used to render a map at `MAP_SIDE` and then buy it down
to the provider's 150 000-character data: URL limit by resolution alone: a real
watercolour scan arrived as 400x575 px, and the labels printed on it were gone.
Now the pixels are bought with a palette first (16, then 8, then 4 colours), the
size steps down only after that, and when even that leaves the whole map smaller
than `VISION_SIDE`, one window of the map is sent at full render density - the
labels are readable there instead of blurred. A request still carries at most two
pictures, and what a picture cannot carry (the names printed on a map) travels as
text, which no compression touches.
"""
import base64
import io
import json
import random

from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import render_material, run
from solo.analysis_images import (MAP_RENDER_SIDE, MAP_SIDE, MAX_URL, VISION_SIDE,
                                  image_count, map_labels, map_window, render_selected)

BOX = (50, 300, 550, 700)
SIZE = (500, 400)


def _flat():
    return bytes((200, 210, 220)) * (SIZE[0] * SIZE[1])


def _drawing():
    """A printed map: flat rooms, walls, keys - many colours, no noise."""
    random.seed(11)
    image = Image.new('RGB', SIZE, (238, 230, 214))
    draw = ImageDraw.Draw(image)
    for _ in range(120):
        left, top = random.randrange(SIZE[0] - 40), random.randrange(SIZE[1] - 30)
        draw.rectangle((left, top, left + random.randrange(6, 40), top + random.randrange(5, 30)),
                       fill=tuple(random.randrange(40, 240) for _ in range(3)))
    for _ in range(60):
        draw.line((random.randrange(SIZE[0]), random.randrange(SIZE[1]),
                   random.randrange(SIZE[0]), random.randrange(SIZE[1])), fill=(20, 20, 20), width=2)
    return image.tobytes()


def _noise():
    random.seed(5)
    return bytes(random.randrange(256) for _ in range(SIZE[0] * SIZE[1] * 3))


def _picture(writer, xobjects, box, pixels):
    left, bottom, right, top = box
    width, height = int(right - left), int(top - bottom)
    stream = DecodedStreamObject()
    stream.set_data(bytes(pixels))
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
    commands = [_picture(writer, xobjects, box, pixels) for box, pixels in pictures]
    for x, y, text in lines:
        escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        commands.append('BT /F1 12 Tf %s %s Td (%s) Tj ET' % (x, y, escaped))
    content = DecodedStreamObject()
    content.set_data(' '.join(commands).encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(content)
    page[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/Font'): fonts, NameObject('/XObject'): xobjects})


def book(tmp_path, pixels, *, labels=False, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        lines = [(60, 260, 'Map of the raiders camp')]
        if labels:
            lines.append((80, 520, 'Camp of the raiders'))
        _page(writer, lines=lines, pictures=[(BOX, pixels)])
    output = io.BytesIO()
    writer.write(output)
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Клад Королевы Драконов')['id']
    path = tmp_path / 'book.pdf'
    path.write_bytes(output.getvalue())
    job = library.enqueue(bundle, path, 'book.pdf', 'book')
    run(tmp_path, job['id'])
    return library


def _decoded(part):
    raw = base64.b64decode(part['image_url']['url'].split(',', 1)[1])
    return Image.open(io.BytesIO(raw))


def _pictures(parts):
    return [part for part in parts if part['type'] == 'image_url']


def _maps(library):
    with library.db() as db:
        return db.execute("SELECT id,document,page FROM materials WHERE kind='map' ORDER BY page").fetchall()


def _render(library, material_id):
    with Image.open(render_material(library, material_id, MAP_RENDER_SIDE)) as image:
        return image.convert('RGB').copy()


def test_a_flat_map_arrives_at_vision_side_and_keeps_its_colours(tmp_path):
    library = book(tmp_path, _flat())
    row = _maps(library)[0]
    parts = render_selected(library, row['document'], [row['page']], 'map')
    pictures = _pictures(parts)
    # A printed drawing in flat colours costs almost nothing: it keeps every
    # colour and the full VISION_SIDE, so no window is needed.
    assert len(pictures) == 1, len(pictures)
    image = _decoded(pictures[0])
    assert max(image.size) == VISION_SIDE, image.size
    assert len(pictures[0]['image_url']['url']) <= MAX_URL
    assert pictures[0]['image_url']['detail'] == 'high'
    assert image_count(parts) == 1


def test_a_coloured_map_is_never_sent_shrunken_below_map_side(tmp_path):
    library = book(tmp_path, _drawing())
    row = _maps(library)[0]
    parts = render_selected(library, row['document'], [row['page']], 'map')
    pictures = _pictures(parts)
    assert pictures
    whole = _decoded(pictures[0])
    assert MAP_SIDE <= max(whole.size) <= VISION_SIDE, whole.size
    assert len(pictures[0]['image_url']['url']) <= MAX_URL
    assert pictures[0]['image_url']['detail'] == 'high'
    assert image_count(parts) == len(pictures)
    # The second picture is a window of the map at full render density: sharper
    # than the whole map, and that is what makes a label inside reachable.
    if len(pictures) > 1:
        window = _decoded(pictures[1])
        assert MAP_SIDE <= max(window.size) <= VISION_SIDE, window.size
        assert len(pictures[1]['image_url']['url']) <= MAX_URL
        assert window.tobytes() != whole.tobytes()


def test_noise_cannot_be_sent_and_is_still_under_the_limit(tmp_path):
    library = book(tmp_path, _noise())
    row = _maps(library)[0]
    parts = render_selected(library, row['document'], [row['page']], 'map')
    pictures = _pictures(parts)
    # A picture with no structure may not fit at MAP_SIDE even in four colours;
    # it is then refused honestly instead of breaking the request.
    assert pictures
    for part in pictures:
        assert len(part['image_url']['url']) <= MAX_URL
        assert part['image_url']['detail'] == 'high'
    assert image_count(parts) == len(pictures)


def test_a_window_is_cut_from_the_full_render_and_centred_on_a_known_point(tmp_path):
    library = book(tmp_path, _drawing())
    rendered = _render(library, _maps(library)[0]['id'])
    # No downscale: the window is a crop of the render the review screen shows.
    corner = map_window(rendered, VISION_SIDE, focus=(0.0, 0.0))
    assert corner.size == (min(VISION_SIDE, rendered.width), min(VISION_SIDE, rendered.height))
    assert corner.tobytes() == rendered.crop((0, 0, corner.width, corner.height)).tobytes()
    bottom = map_window(rendered, VISION_SIDE, focus=(1.0, 1.0))
    assert bottom.size == corner.size
    assert bottom.tobytes() == rendered.crop((rendered.width - bottom.width, rendered.height - bottom.height,
                                              rendered.width, rendered.height)).tobytes()
    assert corner.tobytes() != bottom.tobytes()
    # Nothing known: the window goes where the drawing has the most ink.
    assert map_window(rendered, VISION_SIDE).size == corner.size


def test_two_pages_still_spend_the_two_picture_ceiling(tmp_path):
    library = book(tmp_path, _drawing(), pages=2)
    rows = _maps(library)
    assert len(rows) == 2
    parts = render_selected(library, rows[0]['document'], [row['page'] for row in rows], 'map')
    # Each page keeps one picture: two maps cannot ask for two windows each.
    assert image_count(parts) == 2
    for part in _pictures(parts):
        assert MAP_SIDE <= max(_decoded(part).size) <= VISION_SIDE
        assert len(part['image_url']['url']) <= MAX_URL


def test_the_names_printed_on_a_map_travel_as_text(tmp_path):
    library = book(tmp_path, _flat(), labels=True)
    row = _maps(library)[0]
    parts = render_selected(library, row['document'], [row['page']], 'map')
    texts = [part['text'] for part in parts if part['type'] == 'text']
    # The line printed inside the map's own box is the map's label: it goes to the
    # model as text, where the provider's limit cannot blur it.
    assert any('Camp of the raiders' in text for text in texts), texts


def test_map_labels_reads_the_box_and_the_markup():
    class Library:
        def __init__(self, text, markup):
            self._text = text
            self._row = dict(id='m', markup=markup, kind='map')

        def material_text(self, material):
            return dict(text=self._text, mode='box')

        def material(self, material):
            return dict(self._row)

    assert map_labels(Library('Лагерь, река, мост', ''), 'm') == 'Подписи и места на карте: Лагерь, река, мост'
    # A map drawn as a picture has no text layer; the words of its markup answer.
    markup = json.dumps(dict(type='map', features=['река', 'мост'], summary='лагерь у реки'))
    labels = map_labels(Library('', markup), 'm')
    assert 'река' in labels and 'лагерь у реки' in labels
    # Nothing is known: no invented names.
    assert map_labels(Library('', ''), 'm') == ''
