"""Three small upload fixtures for r229: text PDF, scan PDF, cp1251 TXT.

None of these are book texts: every line was written for this test. They exist
to prove what the import path does with each shape of file a player can bring:

* ``text-layer.pdf`` — three pages with a real text layer;
* ``scan.pdf`` — two pages whose only content is a raster image, no text layer;
* ``russian-cp1251.txt`` — Russian prose saved in cp1251, the usual encoding of
  downloaded Russian books.

Run ``python tests/fixtures/books/make_books.py`` to regenerate the binaries.
"""
import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _font(writer):
    from pypdf.generic import DictionaryObject, NameObject
    return writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
        NameObject('/Encoding'): NameObject('/WinAnsiEncoding')}))


def _text_page(writer, lines, leading=14, size=10):
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    page = writer.add_blank_page(width=595, height=842)
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): _font(writer)})})
    parts = ['BT /F1 %d Tf' % size]
    for index, line in enumerate(lines):
        safe = line.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)')
        parts.append('1 0 0 1 56 %d Tm (%s) Tj' % (780 - index * leading, safe))
    parts.append('ET')
    stream = DecodedStreamObject()
    stream.set_data(' '.join(parts).encode('latin-1'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    return page


def _image_page(writer, jpeg, width, height, page_width=595, page_height=842):
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject, NumberObject
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
    stream.set_data(b'q 470 0 0 640 60 120 cm /Im0 Do Q')
    page[NameObject('/Contents')] = writer._add_object(stream)
    return page


TEXT_PAGES = [
    ['The Salt Road at Hollin Ford',
     '',
     'Read aloud: The ford is shallow and the water',
     'runs over flat stones. A cart waits on the far',
     'bank with one wheel in the mud.',
     '',
     'Hollin Ford is a small crossing on the salt road.',
     'The ford keeper, Alda Renn, lives in a shed',
     'beside the water and keeps a tally of every cart.',
     '',
     'Table 1: Tolls at the ford',
     'Cart with one horse        5 experience',
     'Cart with two horses      10 experience',
     'Night crossing            15 experience'],
    ['The Keeper Shed',
     '',
     'Read aloud: The shed smells of tar and wet rope.',
     'A lamp burns under a tin shade and the door is',
     'propped open with a stone.',
     '',
     'Alda Renn answers questions only about the ford.',
     'She will not speak about the toll house upstream.',
     '',
     'From the shed the road splits: the salt road runs',
     'north to the toll house, and the mud track runs',
     'east to the peat cuttings.'],
    ['The Toll House',
     '',
     'Read aloud: A stone house stands where the road',
     'narrows. The gate is shut and the bell above it',
     'has no rope.',
     '',
     'Two guards watch the gate from the porch. They are',
     'paid in salt and will open for a cart with a tally.',
     '',
     'The peat cuttings are described on page 2.'],
]

SCAN_LINES = ['SCANNED MAP', 'no text layer']


def text_pdf(path: Path) -> None:
    from pypdf import PdfWriter
    writer = PdfWriter()
    for lines in TEXT_PAGES:
        _text_page(writer, lines)
    with path.open('wb') as stream:
        writer.write(stream)


def scan_pdf(path: Path) -> None:
    from PIL import Image, ImageDraw
    from pypdf import PdfWriter
    frames = []
    for index, line in enumerate(SCAN_LINES):
        image = Image.new('RGB', (470, 640), (238, 232, 216))
        draw = ImageDraw.Draw(image)
        draw.rectangle((30, 30, 440, 610), outline=(60, 40, 30), width=3)
        draw.line((40, 120, 430, 120), fill=(60, 40, 30), width=2)
        draw.line((40, 300, 430, 300), fill=(60, 40, 30), width=2)
        draw.text((60, 60 + index * 20), line, fill=(30, 30, 30))
        buffer = io.BytesIO()
        image.save(buffer, 'JPEG', quality=70)
        frames.append(buffer.getvalue())
    writer = PdfWriter()
    for jpeg in frames:
        _image_page(writer, jpeg, 470, 640)
    with path.open('wb') as stream:
        writer.write(stream)


RUSSIAN = (
    'Соляной тракт у Холлинского брода\n'
    '\n'
    'Читается вслух: брод мелкий, вода идёт по плоским камням.\n'
    'На том берегу ждёт телега, одно колесо увязло в грязи.\n'
    '\n'
    'Холлинский брод — небольшая переправа на соляном тракте.\n'
    'Смотрительница брода Альда Ренн живёт в сарае у воды и ведёт\n'
    'счёт каждой телеге.\n'
    '\n'
    'Таблица 1: Пошлины на броде\n'
    'Телега с одной лошадью        5 опыта\n'
    'Телега с двумя лошадьми      10 опыта\n'
    'Ночная переправа             15 опыта\n'
    '\n'
    'Дальше тракт расходится: соляная дорога идёт на север к\n'
    'заставе, а грязевая тропа — на восток к торфяникам.\n'
)


def txt_cp1251(path: Path) -> None:
    path.write_bytes(RUSSIAN.encode('cp1251'))


def main() -> int:
    text_pdf(HERE / 'text-layer.pdf')
    scan_pdf(HERE / 'scan.pdf')
    txt_cp1251(HERE / 'russian-cp1251.txt')
    for name in ('text-layer.pdf', 'scan.pdf', 'russian-cp1251.txt'):
        print(name, (HERE / name).stat().st_size, 'bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main())
