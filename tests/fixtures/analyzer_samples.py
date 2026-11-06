"""Independent PDF samples for the analyzer task, built here rather than reused.

Scenario 3: a book that recommends exactly one level.
Scenario 5: a mixed book with two text columns, a table, a map and a scanned page.
"""
import io
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject


def text_page(writer, lines, *, x=30, y=750, leading=16, size=11, font='F1'):
    """One page whose text is written with a single simple font stream."""
    page=writer.add_blank_page(width=612,height=792)
    font_obj=DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font_obj)})})
    def esc(value):return value.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
    ops=[f'BT /{font} {size} Tf {x} {y} Td {leading} TL']
    for line in lines:ops.append('('+esc(line)+') Tj T*')
    ops.append('ET')
    stream=DecodedStreamObject();stream.set_data('\n'.join(ops).encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    return page


def two_columns(writer, left, right, *, gap=300, top=740, leading=14, size=10):
    """A real two-column layout: the left column and the right column are
    separate text objects, and a naive extractor interleaves their lines."""
    page=writer.add_blank_page(width=612,height=792)
    font_obj=DictionaryObject({NameObject('/Type'):NameObject('/Font'),
        NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font_obj)})})
    def esc(value):return value.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
    ops=[f'BT /F1 {size} Tf']
    for x,column in ((30,left),(gap,right)):
        y=top
        for line in column:
            ops.append(f'1 0 0 1 {x} {y} Tm ('+esc(line)+') Tj')
            y-=leading
    ops.append('ET')
    stream=DecodedStreamObject();stream.set_data('\n'.join(ops).encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    return page


def blank_page(writer):
    """A page that holds only an image, as a scan does: no text layer at all."""
    return writer.add_blank_page(width=612,height=792)


def pdf(writer):
    out=io.BytesIO();writer.write(out);return out.getvalue()


ONE_LEVEL=[  # scenario 3
    'The Salt Road. An adventure for characters of 7th level.',
    'Read aloud: Wind carries salt across the harbour.',
    'Harbour: Salt lies on every rope of the quay.',
    'Read aloud: The warehouse stands at the end of the pier.',
    'Warehouse: Crates are stacked to the rafters.',
    'Master Oren is in Harbour.',
    "Master Oren knows: The ledger names the buyer.",
    'The pier leads to the warehouse, 60 meters, 2 minutes.',
]

MIXED_BOOK=[  # scenario 5
    'The Broken Lighthouse. A mystery for characters of 4th level.',
    'Read aloud: Grey water climbs the rocks under the light.',
    'Shore: Wet stone shines below the tower.',
    'Read aloud: A lamp still burns at the top of the stairs.',
    'Lamp Room: Oil pools around a fallen chair.',
    'Keeper Alva is in Shore.',
    'Keeper Alva knows: The lens was carried away at night.',
    'Stairs: from Shore to Lamp Room, 40 meters, 1 minute.',
]

REWARDS_TABLE=[
    'Rewards',
    '1  Coins on the counter      25',
    '2  A brass compass           15',
    '3  Letters from the buyer    10',
]

MAP_LINES=[
    'Map',
    '1 Shore',
    '2 Stairs up',
    '3 Lamp Room',
]


def one_level_pdf():
    writer=PdfWriter();text_page(writer,ONE_LEVEL);return pdf(writer)


def mixed_pdf():
    writer=PdfWriter()
    text_page(writer,MIXED_BOOK)
    # A table: the source prints it as rows, so a reward amount sits on one line.
    text_page(writer,REWARDS_TABLE)
    # A map is a drawing with printed labels, not geometrically verified topology.
    text_page(writer,MAP_LINES)
    # A scan: an image page with no extractable text at all.
    blank_page(writer)
    # Two columns of ordinary prose, which a naive extractor interleaves.
    two_columns(writer,
        ['Harbour notes','The keeper kept a log.','Every night the log','was signed twice.'],
        ['Lamp notes','The lens was oiled','before the storm came.','The storm came early.'])
    return pdf(writer)
