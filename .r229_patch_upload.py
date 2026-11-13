"""Одноразовая правка CRLF-файлов solo/source_upload.py и solo/adventure_library.py (r229).

Предел размера должен называть и лимит, и фактический размер: «файл слишком
большой» без числа не говорит человеку, резать на две части или на двадцать.
"""
from pathlib import Path


def patch(path, pairs):
    target = Path(path)
    text = target.read_bytes().decode('utf8')
    assert '\r\n' in text, path
    for old, new in pairs:
        assert text.count(old) == 1, (path, text.count(old), old[:90])
        text = text.replace(old, new)
    target.write_bytes(text.encode('utf8'))
    print('patched', path)


patch('solo/source_upload.py', [(
    "                if size>MAX_FILE:raise ValueError('Файл превышает предел 384 МиБ. Оригинал на вашем диске не изменён.')\r\n",
    "                if size>MAX_FILE:raise ValueError('Файл больше предела 384 МиБ: при остановке уже прочитано '\r\n"
    "                    +str(round(size/1024/1024))+' МиБ. Оригинал на вашем диске не изменён; разделите файл на части.')\r\n"
)])

patch('solo/adventure_library.py', [(
    "        if suffix not in FORMATS or Path(path).stat().st_size>MAX_FILE:raise ValueError('Неподдержанный формат или файл больше 384 МиБ')\r\n",
    "        # Две разные беды — две разные причины: «слишком большой» без числа не\r\n"
    "        # говорит, на сколько частей резать, а «неподдержанный формат» без\r\n"
    "        # расширения не говорит, что именно мы читаем.\r\n"
    "        size=Path(path).stat().st_size\r\n"
    "        if suffix not in FORMATS:raise ValueError('Формат файла \"'+suffix+'\" не поддерживается: '\r\n"
    "            'нужен PDF, TXT, MD, JSON, ZIP или картинка (PNG, JPG, WEBP, SVG).')\r\n"
    "        if size>MAX_FILE:raise ValueError('Файл весит '+str(round(size/1024/1024))+' МиБ, а предел — 384 МиБ. '\r\n"
    "            'Разделите его на части; оригинал на вашем диске не изменён.')\r\n"
)])
