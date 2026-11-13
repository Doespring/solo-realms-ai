"""A book prints its maps and handouts inside its pages, not beside them.

These tests pin the step between "the page was read" and "the picture is a
material a person can confirm": every picture large enough to be a map is cut out
by its printed box, an initial letter is not, a caption names the kind, a
full-page background under a column of text stays a page, and a confirmed map
reaches the campaign and the player screen as the picture itself rather than as a
480 px page thumbnail.
"""
import base64
import io
import json
import sqlite3

from PIL import Image
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import render_material, run
from solo.analysis_images import image_count, png_part
from solo.app import create_app
from solo.contracts import Hero
from solo.preparation import Draft

MAP = (30, 60, 90)
HANDOUT = (200, 180, 120)
ILLUSTRATION = (120, 30, 60)
ORNAMENT = (10, 10, 10)
BACKGROUND = (240, 240, 230)


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
    """Eight printed pages: a map, an initial letter, a handout, a plate, a page,
    two pages of an appendix named «Maps», and a page of a musical appendix.

    The captions are printed just under the pictures, the way a module prints
    them; page 5 prints a column of text over a full-page background, which is the
    page itself and not a separate asset.
    """
    writer = PdfWriter()
    _page(writer, lines=[(60, 740, 'The camp of the raiders lies below the ridge.'),
                         (60, 270, 'Map: the camp of the raiders')],
          pictures=[((50, 300, 550, 700), MAP)])
    _page(writer, lines=[(60, 700, 'The road bends east and the patrols walk it.')],
          pictures=[((60, 600, 110, 660), ORNAMENT)])
    _page(writer, lines=[(90, 290, 'Handout: give this page to the players')],
          pictures=[((80, 320, 520, 720), HANDOUT)])
    _page(writer, lines=[(110, 320, 'Portrait of a dragon at rest')],
          pictures=[((100, 350, 500, 650), ILLUSTRATION)])
    column = [(60, 700 - 18 * index, 'The caravan keeps to the road while the patrol counts its carts.')
              for index in range(14)]
    _page(writer, lines=column, pictures=[((0, 0, 600, 800), BACKGROUND)])
    # An appendix prints its name once and then uncaptioned pages: the heading is
    # the book's own name for the pictures that follow it, and the next heading
    # ends it.
    _page(writer, lines=[(60, 740, 'Appendix B'), (60, 720, 'Maps')],
          pictures=[((100, 340, 500, 700), MAP)])
    _page(writer, lines=[(60, 740, 'The keep of Palisade')],
          pictures=[((60, 300, 540, 720), MAP)])
    _page(writer, lines=[(60, 740, 'Appendix C'), (60, 720, 'Music')],
          pictures=[((100, 330, 500, 690), ILLUSTRATION)])
    # A table of contents names maps on every line; the picture printed beside it
    # is not turned into a map by those lines.
    _page(writer, lines=[(60, 700, 'Map 1-1: The keep............12'),
                         (60, 680, 'Map 2-1: The tomb............20'),
                         (60, 660, 'Map 3-1: The tower............30')],
          pictures=[((80, 300, 520, 640), ILLUSTRATION)])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def imported(tmp_path, purpose='book'):
    library = AdventureLibrary(tmp_path)
    bundle = library.create('Клад Королевы Драконов')['id']
    path = tmp_path / 'book.pdf'
    path.write_bytes(book_pdf())
    job = library.enqueue(bundle, path, 'book.pdf', purpose)
    run(tmp_path, job['id'])
    return library, bundle


def candidates_of(library, bundle):
    return [material for material in library.review(bundle)['materials']
            if material['status'] == 'candidate']


def test_a_map_printed_on_a_page_is_cut_out_as_its_own_candidate(tmp_path):
    library, bundle = imported(tmp_path)
    map_candidate = next(item for item in candidates_of(library, bundle) if item['kind'] == 'map')
    assert map_candidate['page'] == 1
    assert map_candidate['scope'] == 'gm' and map_candidate['reviewed'] == 0
    assert map_candidate['region'] == 'p1.i0'
    # The box is the picture's printed one, in PDF points, so a person can check
    # the cut against the page it came from.
    assert [round(value) for value in json.loads(map_candidate['bounds'])] == [50, 300, 550, 700]
    # The page itself is still a material of its own: a cut-out does not replace it.
    pages = [item for item in library.review(bundle)['materials'] if item['kind'] == 'page']
    assert len(pages) == 9


def test_an_initial_letter_and_a_page_background_are_not_candidates(tmp_path):
    library, bundle = imported(tmp_path)
    pages = sorted({item['page'] for item in candidates_of(library, bundle)})
    # The 50x60 pt ornament of page 2 is a letter, not a map; the full-page
    # background under page 5's column of text is the page itself.
    assert 2 not in pages and 5 not in pages
    assert pages == [1, 3, 4, 6, 7, 8, 9]


def test_the_printed_caption_names_the_kind_and_nothing_is_invented(tmp_path):
    library, bundle = imported(tmp_path)
    found = {item['page']: item for item in candidates_of(library, bundle)}
    kinds = {page: item['kind'] for page, item in found.items()}
    # Pages 6 and 7 print no caption of their own: they are maps because the
    # appendix heading above them says «Maps», and page 8 is not, because the
    # musical appendix replaced that heading.
    assert kinds == {1: 'map', 3: 'handout', 4: 'illustration', 6: 'map', 7: 'map',
                     8: 'illustration', 9: 'illustration'}
    # The caption is kept as the reason a picture was called that, so the kind can
    # be checked against the page instead of trusted.
    assert found[1]['caption'].startswith('Map: the camp')
    assert found[3]['caption'].startswith('Handout: give this page')
    # Page 6 prints nothing beside the picture, and no title is invented for it:
    # the book names no name of its own, so the map is named by its kind and page
    # (r161) and a person can still refer to it and rename it.
    assert found[6]['caption'] == 'Карта · стр. 6'
    assert found[7]['caption'] == 'The keep of Palisade'


def test_a_map_is_rendered_from_its_own_box_and_the_model_keeps_its_limit(tmp_path):
    library, bundle = imported(tmp_path)
    material = next(item for item in candidates_of(library, bundle) if item['kind'] == 'map')
    path = render_material(library, material['id'], 2048)
    with Image.open(path) as image:
        # The picture is 500x400 pt, so the map is 2048x1638 — not the whole page
        # (which would be 2048x2731) and not a 480 px thumbnail.
        assert image.width == 2048 and abs(image.height - 1638) <= 2
        picture = image.convert('RGB')
        assert sum(picture.getpixel((1024, 800))) < 400
        # The model still gets at most 384 px and at most two images per request:
        # the wider game copy must not widen what is paid for.
        part = png_part(picture)
    assert image_count([dict(type='text', text='Карта'), part]) == 1
    raw = base64.b64decode(part['image_url']['url'].split(',', 1)[1])
    with Image.open(io.BytesIO(raw)) as small:
        assert max(small.size) <= 384


def test_a_maps_package_does_not_gain_candidates(tmp_path):
    library, bundle = imported(tmp_path, purpose='maps')
    assert candidates_of(library, bundle) == []
    kinds = {item['kind'] for item in library.review(bundle)['materials']}
    assert kinds == {'map'}


def test_the_materials_inventory_names_candidates_without_closed_text(tmp_path):
    library, bundle = imported(tmp_path)
    index = library.media_index(bundle)
    assert index['kinds'] == {'map': 3, 'handout': 1, 'illustration': 3, 'page': 9}
    assert index['candidates'] == 7
    candidate = next(item for item in index['materials'] if item['status'] == 'candidate')
    assert set(candidate) == {'id', 'document', 'page', 'kind', 'scope', 'status', 'region', 'bounds',
                              'caption', 'reviewed', 'sanitized'}
    assert 'СЕКРЕТ' not in str(index)


def test_a_library_built_before_regions_is_rebuilt_and_keeps_its_materials(tmp_path):
    path = tmp_path / 'adventure.sqlite3'
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE documents(id TEXT PRIMARY KEY,format TEXT NOT NULL,bytes INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',pages INTEGER NOT NULL DEFAULT 0,quality TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE materials(id TEXT PRIMARY KEY,document TEXT NOT NULL REFERENCES documents(id),
            page INTEGER NOT NULL,kind TEXT NOT NULL,scope TEXT NOT NULL DEFAULT 'gm',
            public_name TEXT NOT NULL DEFAULT '',reviewed INTEGER NOT NULL DEFAULT 0,
            sanitized INTEGER NOT NULL DEFAULT 0,geometry TEXT,variant TEXT NOT NULL DEFAULT '',
            public_text TEXT NOT NULL DEFAULT '',UNIQUE(document,page));""")
    db.execute("INSERT INTO documents VALUES('d','.pdf',1,'extracted',1,'{}')")
    db.execute("INSERT INTO materials(id,document,page,kind,scope,public_name,reviewed,sanitized,geometry,variant,public_text) "
               "VALUES('m','d',1,'map','player','Старая карта',1,1,NULL,'','текст раздатки')")
    db.commit()
    db.close()
    library = AdventureLibrary(tmp_path)
    kept = library.material('m')
    assert (kept['public_name'], kept['status'], kept['region'], kept['bounds']) == ('Старая карта', 'confirmed', '', None)
    # A second material on the same page is what a cut-out picture is.
    with library.db() as db:
        db.execute("INSERT INTO materials(id,document,page,kind,scope,region,bounds,status) "
                   "VALUES('m2','d',1,'handout','gm','p1.i0','[1,2,3,4]','candidate')")
    assert library.material('m2')['kind'] == 'handout'
