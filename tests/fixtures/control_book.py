"""Control PDF: an independent book the extractor must handle unchanged.

The book is deliberately unlike the Hoard of the Dragon Queen: different names,
different structure, English prose, a two-column spread, a table, a scanned
page with no text layer, a map page, a handout, a fork and cross-page
references. Nothing here records what the answers should be; the expected facts
live in tests/fixtures/control-book-expected.json and are never read by the
extractor.
"""
import io
from pypdf import PdfWriter
from pypdf.generic import (DictionaryObject, NameObject, DecodedStreamObject,
                           NumberObject, ArrayObject)


def _font(writer):
    return writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
        NameObject('/Encoding'): NameObject('/WinAnsiEncoding')}))


def _text_stream(lines, x, y, leading=15, size=11):
    """Text operators placing each line on its own baseline."""
    parts = ['BT /F1 %d Tf' % size]
    for index, line in enumerate(lines):
        safe = line.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')
        parts.append('1 0 0 1 %d %d Tm (%s) Tj' % (x, y - index * leading, safe))
    parts.append('ET')
    return ' '.join(parts).encode('latin-1')


def _page(writer, blocks, width=612, height=792):
    page = writer.add_blank_page(width=width, height=height)
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): _font(writer)})})
    stream = DecodedStreamObject()
    stream.set_data(b'\n'.join(blocks))
    page[NameObject('/Contents')] = writer._add_object(stream)
    return page


def _image_page(writer, jpeg, width, height, page_width=612, page_height=792):
    """A page whose only content is a raster image: no text layer at all."""
    page = writer.add_blank_page(width=page_width, height=page_height)
    image = DecodedStreamObject()
    image.set_data(jpeg)
    image.update({NameObject('/Type'): NameObject('/XObject'),
                  NameObject('/Subtype'): NameObject('/Image'),
                  NameObject('/Width'): NumberObject(width),
                  NameObject('/Height'): NumberObject(height),
                  NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
                  NameObject('/BitsPerComponent'): NumberObject(8),
                  NameObject('/Filter'): NameObject('/DCTDecode')})
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/XObject'): DictionaryObject({NameObject('/Im0'): writer._add_object(image)})})
    stream = DecodedStreamObject()
    stream.set_data(b'q 512 0 0 662 50 65 cm /Im0 Do Q')
    page[NameObject('/Contents')] = writer._add_object(stream)
    return page


# --- the book ------------------------------------------------------------

PAGE_1_LEFT = [
    'The Lantern Quarry',
    'Chapter One: The Bell Landing',
    '',
    'Read aloud: Fog sits on the water and the bell',
    'rope knocks against its post. A ferry waits,',
    'half loaded, and nobody is holding the line.',
    '',
    'The landing has one lit lamp and a dry step',
    'where a guide waits with a folded chart.',
]

PAGE_1_RIGHT = [
    'Mara Quill, the ferry guide, will not cross',
    'until the bell is rung three times. She keeps',
    'her back to the water and answers in short',
    'sentences.',
    '',
    'From the landing the path splits: the plank',
    'walk runs to the Sunken Gallery, and the',
    'quarry stair climbs to the Overseer Walk.',
    'The gallery is described on page 3.',
    'The stair is described on page 4.',
]

PAGE_2 = [
    'Table 1: Ferry tolls and rewards',
    '',
    'Bell Landing entry          10 experience',
    'Sunken Gallery cleared      40 experience',
    'Overseer Walk inspected     25 experience',
    'Ferryman letter handed in   15 experience',
    '',
    'A reward is paid once and only when the',
    'written line above is met. A mention of a',
    'creature is not a reward and not a fight.',
]

PAGE_3 = [
    'The Sunken Gallery',
    '',
    'Read aloud: Water covers the floor to the',
    'ankle. Lantern light stops at the first arch.',
    '',
    'Three kobolds guard the far arch and will',
    'not leave while the water is low. They are',
    'paid in salt and answer to the overseer.',
    '',
    'The gallery can be entered without a fight',
    'if the bell has been rung three times.',
]

PAGE_4 = [
    'The Overseer Walk',
    '',
    'Read aloud: The quarry stair ends at a rail',
    'of rusted iron. Below, the shaft is black.',
    '',
    'Map: The Lantern Quarry shows the landing,',
    'the gallery, the walk and the flooded shaft.',
    'The walk overlooks all four places.',
    '',
    'A marsh drake crosses the sky above the',
    'rail and does not land. It is not an',
    'encounter and takes no turn.',
]

PAGE_5 = [
    'Handout: the ferryman letter',
    '',
    'Hand this page to the players without',
    'comment. It reads: the salt is counted at',
    'the landing, and the overseer signs for it.',
    '',
    'The letter proves the toll was paid.',
]

PAGE_6 = [
    'The Flooded Shaft',
    '',
    'Read aloud: The shaft is cold and a rope',
    'hangs into the dark without a knot.',
    '',
    'The shaft is reached only from the Overseer',
    'Walk; the gallery does not connect to it.',
    'See page 4 for the walk and page 3 for the',
    'gallery.',
]

SCAN_LINES = [
    'Lantern Quarry - scanned notice',
    'The overseer requires the salt count',
    'at the landing before the bell.',
    'Signed: the overseer, in salt.',
]


def build(path):
    """Write the control book and return its bytes."""
    from pathlib import Path
    from PIL import Image, ImageDraw

    path = Path(path)

    canvas = Image.new('RGB', (1024, 1324), 'white')
    draw = ImageDraw.Draw(canvas)
    for index, line in enumerate(SCAN_LINES):
        draw.text((60, 80 + index * 46), line, fill='black')
    buffer = io.BytesIO()
    canvas.save(buffer, format='JPEG', quality=90)

    writer = PdfWriter()
    _page(writer, [_text_stream(PAGE_1_LEFT, 50, 720),
                   _text_stream(PAGE_1_RIGHT, 320, 720)])
    _page(writer, [_text_stream(PAGE_2, 50, 720)])
    _page(writer, [_text_stream(PAGE_3, 50, 720)])
    _page(writer, [_text_stream(PAGE_4, 50, 720)])
    _page(writer, [_text_stream(PAGE_5, 50, 720)])
    _page(writer, [_text_stream(PAGE_6, 50, 720)])
    _image_page(writer, buffer.getvalue(), 1024, 1324)
    writer.add_metadata({'/Title': 'The Lantern Quarry',
                         '/Author': 'control fixture',
                         '/Subject': 'independent control book for extraction'})
    writer.write(str(path))
    return path.read_bytes()


if __name__ == '__main__':
    import sys
    print(build(sys.argv[1]))
