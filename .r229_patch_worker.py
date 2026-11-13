"""Одноразовая правка CRLF-файла solo/adventure_worker.py (r229)."""
from pathlib import Path

PATH = Path('solo/adventure_worker.py')
text = PATH.read_bytes().decode('utf8')
assert '\r\n' in text


def replace(old, new, count=1):
    global text
    assert text.count(old) == count, (text.count(old), old[:80])
    text = text.replace(old, new)


# --- 1. Распознавание кодировки и честный отказ вместо кракозябр ------------
HELPERS = '''
#: Кодировки, в которых к нам реально приходит русский текст. Раньше пробовались
#: только UTF-8 и windows-1251, поэтому файл в DOS-866 молча превращался в
#: кракозябры, а выглядело это как отказ модели на структуре.
TEXT_ENCODINGS=('utf-8-sig','cp1251','cp866','koi8-r')
#: Частые пары русских букв. В правильном тексте их много, в тексте, прочитанном
#: не той кодировкой, почти нет — поэтому чужое прочтение не выиграет случайно.
RUSSIAN_BIGRAMS=('ст','но','то','на','ен','ов','ни','ра','ко','по','пр','ро','ос','го','ер','ал',
    'ли','ре','ка','та','ет','ла','ит','за','чт','ль','ол','де','ат','ьн','ый','ой','ам','ах','ом',
    'ег','ск','ва','не','чн','жд','ут','ад','ел','ест','ени','ого')
#: Ниже этого счёта прочтение не похоже на прозу: под именем .txt лежит не текст.
#: Лучше честный отказ с указанием, что делать, чем 400 страниц мусора в библиотеке.
TEXT_SCORE_FLOOR=0.5

def text_score(text):
    """Насколько прочтение похоже на прозу: буквы плюс пары русских букв."""
    length=max(1,len(text))
    letters=sum(character.isalpha() for character in text)
    broken=sum(1 for character in text if unicodedata.category(character) in ('Cc','Cs','Co','Cf')
               and character not in '\\n\\r\\t')+text.count('\\ufffd')
    lower=text.lower();bigrams=sum(lower.count(pair) for pair in RUSSIAN_BIGRAMS)
    return letters/length+3*bigrams/length-20*broken/length

def decode_text(raw):
    """Прочитать текстовый источник в (текст, кодировка); мусор не возвращается.

    Кракозябры опаснее отказа: они уходят в модель, модель отвечает «не понял», и
    человек видит «ответ не прошёл проверку структуры» вместо «файл не в той
    кодировке». Поэтому прочтение выбирается по счёту, а прочтение, не похожее
    на прозу, отклоняется с указанием, что именно сделать с файлом.
    """
    candidates=[]
    if raw[:2] in (b'\\xff\\xfe',b'\\xfe\\xff') or raw.count(b'\\x00')>len(raw)//4:
        for encoding in ('utf-16','utf-16-le','utf-16-be'):
            try:candidates.append((encoding,raw.decode(encoding)))
            except UnicodeError:pass
    for encoding in TEXT_ENCODINGS:
        try:candidates.append((encoding,raw.decode(encoding)))
        except UnicodeError:pass
    if not candidates:
        raise ValueError('Кодировка текстового файла не распознана: проверены UTF-8, Windows-1251, '
                         'DOS-866 и KOI8-R. Пересохраните файл в UTF-8; оригинал сохранён без изменений.')
    encoding,text=max(candidates,key=lambda item:text_score(item[1]))
    if text_score(text)<TEXT_SCORE_FLOOR:
        raise ValueError('Файл не похож на текст: читаемой прозы нет ни в UTF-8, ни в Windows-1251, '
                         'DOS-866 или KOI8-R. Похоже, это не текстовая книга; оригинал сохранён без изменений.')
    return text,encoding

def discard_document(library,sha,reason,quality=None):
    """Убрать строки и отрисованные страницы источника, из которого ничего не вышло.

    Отказанный файл не должен оставлять в библиотеке страницы, материалы и
    кандидатов: тогда человек видит страницу, которую нельзя прочитать, как
    будто это годный источник, а подготовка падает позже и по другой причине.
    """
    import sqlite3
    with library.db() as db:
        for (pid,) in db.execute('SELECT id FROM pages WHERE document=?',(sha,)).fetchall():db.execute('DELETE FROM pages_fts WHERE rowid=?',(pid,))
        db.execute('DELETE FROM pages WHERE document=?',(sha,))
        db.execute('DELETE FROM materials WHERE document=?',(sha,))
        db.execute('DELETE FROM candidates WHERE document=?',(sha,))
        try:db.execute('DELETE FROM pdf_layout WHERE document=?',(sha,))
        except sqlite3.OperationalError:pass
        body=dict(quality or {},reason=reason,mechanics='requires_review')
        db.execute('UPDATE documents SET status="failed",pages=0,quality=? WHERE id=?',(json.dumps(body,ensure_ascii=False),sha))
    for path in library.files.glob(sha+'-p*'):
        if path.is_file():path.unlink()

'''
replace('\r\ndef candidate_kind(line):', HELPERS.replace('\n', '\r\n') + '\r\ndef candidate_kind(line):')

# --- 2. Счётчики: буквы во всём документе и номера пустых страниц -----------
replace('    path=library.blob(sha);total_text=0;blank=0;partial=[]\r\n',
        '    path=library.blob(sha);total_text=0;total_letters=0;blank=0;partial=[];blank_pages=[];text_encoding=None\r\n')
replace("            if not 1<=count<=2000:raise ValueError('Лимит страниц PDF')\r\n",
        "            if not 1<=count<=2000:raise ValueError('В PDF '+str(count)+' страниц, а предел — 2000. '\r\n"
        "                'Разделите книгу на части; оригинал сохранён без изменений.')\r\n")
replace("                        if total[0]>12000000:raise ValueError('Лимит извлечённого текста')\r\n",
        "                        if total[0]>12000000:raise ValueError('Из PDF извлекается больше 12 млн знаков: это больше, '\r\n"
        "                            'чем держит один импорт. Разделите книгу на части; оригинал сохранён.')\r\n")
replace("                    total_text+=len(text)\r\n"
        "                    if total_text>12000000:raise ValueError('Лимит извлечённого текста')\r\n"
        "                    if len(re.sub(r'\\W','',text))<30:blank+=1\r\n",
        "                    total_text+=len(text);letters=len(re.sub(r'\\W','',text));total_letters+=letters\r\n"
        "                    if total_text>12000000:raise ValueError('Из PDF извлекается больше 12 млн знаков: это больше, '\r\n"
        "                        'чем держит один импорт. Разделите книгу на части; оригинал сохранён.')\r\n"
        "                    if letters<30:blank+=1;blank_pages.append(index+1)\r\n")

# --- 3. Отказ на скан: ноль знаков до модели не доходит ---------------------
replace("                if job:library.progress(job,done=index+1)\r\n"
        "    elif format in ('.png','.jpg','.jpeg','.webp','.svg'):\r\n",
        "                if job:library.progress(job,done=index+1)\r\n"
        "        # Во всём PDF не нашлось букв: это скан. Распознавания (OCR) у нас нет, и\r\n"
        "        # источник, который доходит до модели нулём знаков, хуже честного отказа:\r\n"
        "        # подготовка падала бы позже и выглядела бы как поломка модели. Строки\r\n"
        "        # убираем, чтобы в библиотеке не осталось непрочитываемых страниц. Карты и\r\n"
        "        # раздатки (purpose != 'book') — наоборот, часто чистые картинки, их не трогаем.\r\n"
        "        if purpose=='book' and total_letters<30:\r\n"
        "            reason=('В этом PDF нет текстового слоя: распознавание (OCR) не поддерживается. '\r\n"
        "                    'Нужен PDF с текстом или TXT.')\r\n"
        "            discard_document(library,sha,reason,dict(text_characters=total_text,blank_pages=blank,\r\n"
        "                blank_pages_list=blank_pages,pictured_pages=[],ocr=False,text_encoding=None))\r\n"
        "            raise ValueError(reason)\r\n"
        "    elif format in ('.png','.jpg','.jpeg','.webp','.svg'):\r\n")

# --- 4. TXT/MD: распознать кодировку, а не отдавать кракозябры --------------
replace("        if path.stat().st_size>12000000:raise ValueError('Лимит текстового документа 12 МБ; разделите текст на части.')\r\n"
        "        raw=path.read_bytes()\r\n"
        "        if len(raw)>12000000:raise ValueError('Лимит текстового документа')\r\n"
        "        try:text=raw.decode('utf-8-sig')\r\n"
        "        except UnicodeError:text=raw.decode('cp1251')\r\n",
        "        size=path.stat().st_size\r\n"
        "        if size>12000000:raise ValueError('Текстовый файл весит '+str(round(size/1024/1024,1))+' МБ, '\r\n"
        "            'а предел — 12 МБ. Разделите текст на части; оригинал сохранён без изменений.')\r\n"
        "        raw=path.read_bytes()\r\n"
        "        if len(raw)>12000000:raise ValueError('Текстовый файл весит '+str(round(len(raw)/1024/1024,1))+' МБ, '\r\n"
        "            'а предел — 12 МБ. Разделите текст на части; оригинал сохранён без изменений.')\r\n"
        "        try:text,text_encoding=decode_text(raw)\r\n"
        "        except ValueError as error:\r\n"
        "            # Источник не читается: строк в библиотеке ещё нет, но отметку об\r\n"
        "            # отказе и причину человек должен видеть в карточке комплекта.\r\n"
        "            discard_document(library,sha,str(error),dict(text_characters=0,ocr=False,text_encoding=None))\r\n"
        "            raise\r\n")

# --- 5. Кодировка и текст в отчёте о качестве ------------------------------
replace("    quality=dict(text_characters=total_text,blank_pages=blank,partial_pages=partial,mechanics='requires_review',media_scope='gm',ocr=False)\r\n",
        "    quality=dict(text_characters=total_text,text_encoding=text_encoding,blank_pages=blank,partial_pages=partial,\r\n"
        "                 mechanics='requires_review',media_scope='gm',ocr=False,blank_pages_list=blank_pages)\r\n")

# --- 6. Причина отказа — в сообщении задания, а не только в классе ошибки ---
replace("        message='Материалы извлечены. Подготовка правил, событий и доступа ещё требует проверки.'\r\n",
        "        with library.db() as db:characters=db.execute('SELECT coalesce(sum(length(content)),0) FROM pages WHERE document=?',(record['document'],)).fetchone()[0]\r\n"
        "        message='Извлечено знаков: '+str(characters)+'. Подготовка правил, событий и доступа ещё требует проверки.'\r\n")
replace("            message='Извлечено страниц: '+str(summary['pages'])+'. Модулей: '+str(len(summary['modules']))+'.'\r\n",
        "            message='Извлечено страниц: '+str(summary['pages'])+', знаков: '+str(characters)+'. Модулей: '+str(len(summary['modules']))+'.'\r\n")
replace("        except ValueError as error:summary=None;message='Страницы извлечены, нарезка модулей не выполнена: '+str(error)\r\n",
        "        except ValueError as error:summary=None;message='Извлечено знаков: '+str(characters)+'. Нарезка модулей не выполнена: '+str(error)\r\n")
replace("    except BaseException as error:\r\n"
        "        library.progress(job,status='failed',message='Извлечение не завершено ('+type(error).__name__+'). Оригинал сохранён; непроверенные материалы не готовы к игре.')\r\n"
        "        raise\r\n",
        "    except BaseException as error:\r\n"
        "        # Причина — в сообщении, а не только в классе ошибки: «Извлечение не\r\n"
        "        # завершено (ValueError)» ничего не говорит человеку о том, что делать.\r\n"
        "        reason=str(error).strip() or type(error).__name__\r\n"
        "        library.progress(job,status='failed',message='Извлечение не завершено: '+reason[:600]+\r\n"
        "            ' Оригинал сохранён; непроверенные материалы не готовы к игре.')\r\n"
        "        raise\r\n")

PATH.write_bytes(text.encode('utf8'))
print('patched', PATH)
