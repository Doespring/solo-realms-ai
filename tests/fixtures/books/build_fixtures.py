"""Свои короткие фрагменты для r229: три файла, которые проверяет загрузка.

Тексты здесь наши собственные и нарочно короткие: в репозиторий не попадают
ни книжные страницы, ни чужие переводы. Запуск пересоздаёт файлы байт-в-байт:

    .venv/bin/python tests/fixtures/books/build_fixtures.py

Три случая, которые видит владелец:
  * ``text-layer.pdf`` — PDF с текстовым слоем, 3 страницы, главы и обрывки прозы;
  * ``scan-only.pdf`` — те же страницы, но только пикселями: текстового слоя нет,
    распознавания (OCR) в приложении нет, и это должно быть сказано человеку;
  * ``cp1251.txt`` — русский текст в windows-1251, как в большинстве книг,
    скачанных из русских библиотек.
"""
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

HERE = Path(__file__).resolve().parent

PAGES = [
    [
        'Chapter 1. The Salt Gate',
        'The gate opens at dawn. Salt lies on every rope of the quay.',
        'Read aloud: Wind carries salt across the harbour.',
        'Harbour: wet stone shines under the lamp.',
        'Keeper Alva waits by the gate.',
        'Alva knows: the ledger names the buyer.',
    ],
    [
        'Chapter 2. The Warehouse',
        'Crates are stacked to the rafters of the warehouse.',
        'Read aloud: Someone has been sleeping among the crates.',
        'Warehouse: the floor is scratched near the back wall.',
        'The pier leads to the warehouse, 60 meters, 2 minutes.',
    ],
    [
        'Chapter 3. The Lamp Room',
        'The lamp still burns at the top of the stairs.',
        'Read aloud: Oil pools around a fallen chair.',
        'Lamp Room: a lens lies face down in the dust.',
        'Stairs: from the warehouse to the lamp room, 40 meters, 1 minute.',
    ],
]

# Тот же текст в windows-1251 — «книга из русской библиотеки».
RUSSIAN = """Глава 1. Соляные ворота
Ворота отворяются на рассвете. Соль лежит на каждой верёвке причала.
Прочитайте вслух: ветер несёт соль через гавань.
Гавань: мокрый камень блестит под фонарём.
Смотрительница Алва ждёт у ворот.
Алва знает: в книге учёта записан покупатель.

Глава 2. Склад
Ящики стоят до самых стропил склада.
Прочитайте вслух: кто-то спал среди ящиков.
Склад: пол исцарапан у задней стены.
Причал ведёт к складу, 60 метров, 2 минуты.
"""


def _font():
    obj = DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
    })
    return obj


def _escape(value):
    return value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def text_page(writer, lines):
    page = writer.add_blank_page(width=612, height=792)
    font = writer._add_object(_font())
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
    })
    ops = ['BT /F1 11 Tf 40 740 Td 16 TL']
    for line in lines:
        ops.append('(' + _escape(line) + ') Tj T*')
    ops.append('ET')
    stream = DecodedStreamObject()
    stream.set_data('\n'.join(ops).encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    return page


def text_layer_pdf():
    writer = PdfWriter()
    for lines in PAGES:
        text_page(writer, lines)
    out = HERE / 'text-layer.pdf'
    with out.open('wb') as stream:
        writer.write(stream)
    return out


def scan_only_pdf():
    """Только пиксели: PDF, снятый «сканером», без единого текстового объекта."""
    images = []
    for number, lines in enumerate(PAGES, 1):
        image = Image.new('L', (720, 960), 255)
        draw = ImageDraw.Draw(image)
        y = 60
        for line in lines:
            draw.text((40, y), line, fill=30)
            y += 26
        draw.text((40, 900), 'scan page ' + str(number), fill=120)
        images.append(image)
    out = HERE / 'scan-only.pdf'
    images[0].save(out, 'PDF', resolution=100.0, save_all=True, append_images=images[1:])
    return out


def cp1251_txt():
    out = HERE / 'cp1251.txt'
    out.write_bytes(RUSSIAN.replace('\n', '\r\n').encode('cp1251'))
    return out


def main():
    HERE.mkdir(parents=True, exist_ok=True)
    for build in (text_layer_pdf, scan_only_pdf, cp1251_txt):
        path = build()
        print(path.name, path.stat().st_size, 'bytes')


if __name__ == '__main__':
    main()
