"""Bounded import/render worker, isolated from the interactive backend process."""
import io,json,os,re,stat,sys,unicodedata,zipfile,tempfile
from pathlib import Path,PurePosixPath
from .adventure_library import AdventureLibrary,MAX_FILE,MAX_PACKAGE,MAX_FILES,FORMATS,stable

def zip_name(entry):
    name=entry.filename
    if not entry.flag_bits & 0x800:
        try:
            raw=name.encode('cp437');alternatives=[name]
            for encoding in ('utf8','cp866','cp1251'):
                try:alternatives.append(raw.decode(encoding))
                except UnicodeError:pass
            name=max(alternatives,key=lambda value:sum('\u0400'<=c<='\u04ff' for c in value))
        except UnicodeError:pass
    name=unicodedata.normalize('NFC',name).replace('\\','/')
    path=PurePosixPath(name)
    if (not name or len(name)>500 or name.startswith('/') or ':' in name or '\x00' in name
            or any(p in ('.','..') for p in name.split('/')) or stat.S_ISLNK(entry.external_attr>>16)):
        raise ValueError('Недопустимый путь в ZIP')
    return name

def svg_safe(raw):
    import xml.etree.ElementTree as ET
    if len(raw)>1024*1024 or re.search(br'<!DOCTYPE|<!ENTITY',raw,re.I):raise ValueError('Небезопасный SVG')
    root=ET.fromstring(raw)
    tags={'svg','g','path','rect','circle','ellipse','line','polyline','polygon','title','desc'}
    attributes={'viewBox','width','height','d','x','y','x1','x2','y1','y2','cx','cy','r','rx','ry','points','fill','stroke','stroke-width','opacity','fill-opacity','stroke-opacity','transform','fill-rule','stroke-linecap','stroke-linejoin'}
    nodes=list(root.iter())
    if len(nodes)>5000:raise ValueError('Слишком сложный SVG')
    for node in nodes:
        if node.tag.split('}')[-1] not in tags:raise ValueError('Небезопасный SVG-элемент')
        for key,value in node.attrib.items():
            if key not in attributes or re.search(r'url\s*\(|javascript|https?:|data:',value,re.I):raise ValueError('Небезопасный SVG-атрибут')
    if root.tag.split('}')[-1]!='svg':raise ValueError('Не SVG')
    return ET.tostring(root,encoding='utf8')

def render_material(library,id,width=1000):
    material=library.material(id);width=max(160,min(int(width),4096))
    with library.db() as db:document=dict(db.execute('SELECT * FROM documents WHERE id=?',(material['document'],)).fetchone())
    suffix='.svg' if document['format']=='.svg' else '.png'
    from .module_media import crop_box,render_name
    output=library.files/render_name(material['document'],material['page'],material.get('region'),width,suffix)
    if output.exists():return output
    from PIL import Image,ImageOps
    Image.MAX_IMAGE_PIXELS=16777216
    if document['format']=='.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(library.blob(material['document'])) as pdf:
            page=pdf[material['page']-1]
            try:
                w,h=page.get_size()
                # A picture cut out of a page is rendered from its own printed box at
                # the requested width, so a map is not shipped as a 480 px page
                # thumbnail. Bounds are PDF points with a bottom-left origin, which is
                # also what pdfium crops in; a rotated page falls back to the page.
                box=crop_box(material,w,h) if not page.get_rotation() else None
                if box:
                    left,bottom,right,top=box;side=max(right-left,top-bottom)
                    scale=min(width/side,4096/side)
                    bitmap=page.render(scale=scale,crop=(left,bottom,max(0.0,w-right),max(0.0,h-top)))
                else:
                    scale=min(width/max(w,h),4096/max(w,h))
                    bitmap=page.render(scale=scale)
                try:image=bitmap.to_pil().copy()
                finally:bitmap.close()
            finally:page.close()
    elif document['format']=='.svg':
        temporary=output.with_suffix('.tmp');temporary.write_bytes(svg_safe(library.blob(material['document']).read_bytes()));temporary.replace(output);return output
    else:
        with Image.open(library.blob(material['document'])) as original:
            if original.width*original.height>16777216:raise ValueError('Изображение слишком велико')
            image=ImageOps.exif_transpose(original).convert('RGBA');image.thumbnail((width,width))
    temporary=output.with_suffix('.tmp');image.save(temporary,'PNG');image.close();temporary.replace(output)
    return output

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
               and character not in '\n\r\t')+text.count('\ufffd')
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
    if raw[:2] in (b'\xff\xfe',b'\xfe\xff') or raw.count(b'\x00')>len(raw)//4:
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
    # «UTF-8» и «UTF-8 с BOM» читаются одним кодеком: в отчёт идёт то, что
    # действительно лежит в файле, иначе метрика врёт про лишние три байта.
    if encoding=='utf-8-sig' and raw[:3]!=b'\xef\xbb\xbf':encoding='utf-8'
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


def candidate_kind(line):
    if re.search(r'\bDDEX\d+-\d+\b',line,re.I):return 'adventure'
    # «appendix» — тот же заголовок, что «приложение»: без него английская книга
    # не показывала приложение импорту, и его страницы молча становились прозой.
    if re.match(r'\s*(глава|эпизод|приложение|chapter|appendix)\b',line,re.I):return 'chapter'
    if re.match(r'\s*(таблица|table)\b',line,re.I):return 'table'
    if re.match(r'\s*(раздат|handout|карта|map)\b',line,re.I):return 'media'
    if re.match(r'\s*(если|через|каждый|каждую)\b',line,re.I):return 'instruction'
    return None

def extract_document(library,sha,format,purpose,job):
    with library.db() as db:
        previous=db.execute('SELECT status FROM documents WHERE id=?',(sha,)).fetchone()
        if previous and previous[0]=='extracted':return
        db.execute('UPDATE documents SET status="extracting" WHERE id=?',(sha,))
    path=library.blob(sha);total_text=0;total_letters=0;blank=0;partial=[];blank_pages=[];text_encoding=None
    if format=='.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(path) as pdf:
            count=len(pdf)
            if not 1<=count<=2000:raise ValueError('В PDF '+str(count)+' страниц, а предел — 2000. '
                'Разделите книгу на части; оригинал сохранён без изменений.')
            if job:library.progress(job,total=count)
            section=None
            # Lines the book prints on more than one page are its running heads,
            # not the names of the pictures beside them. They are counted before
            # the pages are laid out, because a head printed on page 8 can only be
            # recognised as repeated by the pages that follow it.
            repeated=set()
            if purpose=='book':
                from .module_media import repeated_lines
                from .pdf_pipeline import MAX_PAGE_CHARS
                total=[0]
                def printed_pages():
                    for number in range(count):
                        block=pdf[number]
                        try:
                            textpage=block.get_textpage()
                            try:value=textpage.get_text_range() or ''
                            finally:textpage.close()
                        finally:block.close()
                        if len(value)>MAX_PAGE_CHARS:raise ValueError('Текст страницы превышает безопасный лимит; исходный PDF сохранён')
                        total[0]+=len(value)
                        if total[0]>12000000:raise ValueError('Из PDF извлекается больше 12 млн знаков: это больше, '
                            'чем держит один импорт. Разделите книгу на части; оригинал сохранён.')
                        yield value
                repeated=repeated_lines(printed_pages())
            for index in range(count):
                page=pdf[index]
                try:
                    textpage=page.get_textpage()
                    try:
                        text=textpage.get_text_range() or ''
                        from .pdf_pipeline import layout_page,MAX_PAGE_CHARS
                        if len(text)>MAX_PAGE_CHARS:raise ValueError('Текст страницы превышает безопасный лимит; исходный PDF сохранён')
                        layout=layout_page(page,textpage,text,index+1)
                    finally:textpage.close()
                    total_text+=len(text);letters=len(re.sub(r'\W','',text));total_letters+=letters
                    if total_text>12000000:raise ValueError('Из PDF извлекается больше 12 млн знаков: это больше, '
                        'чем держит один импорт. Разделите книгу на части; оригинал сохранён.')
                    if letters<30:blank+=1;blank_pages.append(index+1)
                    images=[image['bounds'] for image in layout['images']]
                    w,h=page.get_size()
                    label=pdf.get_page_label(index) or ''
                finally:page.close()
                kind='map' if purpose=='maps' else 'handout' if purpose=='handouts' else 'page'
                with library.db() as db:
                    old=db.execute('SELECT id FROM pages WHERE document=? AND number=?',(sha,index+1)).fetchone()
                    if old:db.execute('DELETE FROM pages_fts WHERE rowid=?',(old[0],))
                    db.execute('INSERT INTO pages(document,number,label,content,objects) VALUES(?,?,?,?,?) ON CONFLICT(document,number) DO UPDATE SET label=excluded.label,content=excluded.content,objects=excluded.objects',(sha,index+1,label,text,json.dumps(dict(images=images,bounds=[0,0,w,h]))))
                    pid=db.execute('SELECT id FROM pages WHERE document=? AND number=?',(sha,index+1)).fetchone()[0]
                    db.execute('INSERT INTO pages_fts(rowid,content) VALUES(?,?)',(pid,text))
                    from .pdf_pipeline import save_layout
                    save_layout(db,sha,index+1,text,layout)
                    mid=stable(sha,index+1)
                    db.execute('INSERT OR IGNORE INTO materials(id,document,page,kind) VALUES(?,?,?,?)',(mid,sha,index+1,kind))
                    # A bought module prints its maps and handouts inside the page, so
                    # every picture the layout found becomes its own material: named
                    # by its printed region and box, offered as a candidate, and
                    # playable only after a person confirms it in the review screen.
                    # Its file is cut out of the page on first request, not here: a
                    # book can hold hundreds of pictures and the import must stay
                    # inside its time and memory budget.
                    if purpose=='book':
                        from .module_media import candidates as page_media,section_kind
                        announced=section_kind(text)
                        if announced is not None:section=announced
                        for item in page_media(layout,text,section,repeated,page=index+1):
                            rid=stable(sha,index+1,item['region'])
                            # The review screen shows the caption; a picture the page did
                            # not name carries its kind and page (r161), and a clean form of
                            # that name is kept as the public name, so confirming a candidate
                            # does not start from an empty field.
                            db.execute('INSERT OR IGNORE INTO materials(id,document,page,kind,scope,public_name,region,bounds,caption,status) VALUES(?,?,?,?,?,?,?,?,?,?)',(rid,sha,index+1,item['kind'],'gm',item.get('public_name') or item['caption'],item['region'],json.dumps(item['bounds']),item['caption'],'candidate'))
                    for line in text.splitlines():
                        line=line.strip()[:180];kind_hint=candidate_kind(line)
                        if kind_hint:
                            cid=stable(sha,index+1,kind_hint,line)
                            db.execute('INSERT OR IGNORE INTO candidates(id,document,page,kind,title,body) VALUES(?,?,?,?,?,?)',(cid,sha,index+1,kind_hint,line,json.dumps({'scope':'gm','confidence':'candidate','printed_page':label})))
                # Render all standalone map/handout PDF pages, rather than counting filenames.
                if purpose in ('maps','handouts') or index==0:render_material(library,mid,480)
                if job:library.progress(job,done=index+1)
        # Во всём PDF не нашлось букв: это скан. Распознавания (OCR) у нас нет, и
        # источник, который доходит до модели нулём знаков, хуже честного отказа:
        # подготовка падала бы позже и выглядела бы как поломка модели. Строки
        # убираем, чтобы в библиотеке не осталось непрочитываемых страниц. Карты и
        # раздатки (purpose != 'book') — наоборот, часто чистые картинки, их не трогаем.
        if purpose=='book' and total_letters<30:
            reason=('В этом PDF нет текстового слоя: распознавание (OCR) не поддерживается. '
                    'Нужен PDF с текстом или TXT.')
            discard_document(library,sha,reason,dict(text_characters=total_text,blank_pages=blank,
                blank_pages_list=blank_pages,pictured_pages=[],ocr=False,text_encoding=None))
            raise ValueError(reason)
    elif format in ('.png','.jpg','.jpeg','.webp','.svg'):
        count=1;mid=stable(sha,1)
        with library.db() as db:db.execute('INSERT OR IGNORE INTO materials(id,document,page,kind) VALUES(?,?,?,?)',(mid,sha,1,'map' if purpose=='maps' else 'handout' if purpose=='handouts' else 'illustration'))
        render_material(library,mid,480)
    else:
        size=path.stat().st_size
        if size>12000000:raise ValueError('Текстовый файл весит '+str(round(size/1024/1024,1))+' МБ, '
            'а предел — 12 МБ. Разделите текст на части; оригинал сохранён без изменений.')
        raw=path.read_bytes()
        if len(raw)>12000000:raise ValueError('Текстовый файл весит '+str(round(len(raw)/1024/1024,1))+' МБ, '
            'а предел — 12 МБ. Разделите текст на части; оригинал сохранён без изменений.')
        try:text,text_encoding=decode_text(raw)
        except ValueError as error:
            # Источник не читается: строк в библиотеке ещё нет, но отметку об
            # отказе и причину человек должен видеть в карточке комплекта.
            discard_document(library,sha,str(error),dict(text_characters=0,ocr=False,text_encoding=None))
            raise
        count=1;total_text=len(text);blank=int(len(re.sub(r'\W','',text))<30)
        with library.db() as db:
            old=db.execute('SELECT id FROM pages WHERE document=? AND number=1',(sha,)).fetchone()
            if not old:
                pid=db.execute('INSERT INTO pages(document,number,content) VALUES(?,1,?)',(sha,text)).lastrowid
                db.execute('INSERT INTO pages_fts(rowid,content) VALUES(?,?)',(pid,text))
        if format=='.json':
            value=json.loads(text)
            # Structured instructions remain unconfirmed until source review.
            if value.get('adventure_definition'):
                from .adventure_contracts import AdventureDefinition
                AdventureDefinition.model_validate(value['adventure_definition'])
    quality=dict(text_characters=total_text,text_encoding=text_encoding,blank_pages=blank,partial_pages=partial,
                 mechanics='requires_review',media_scope='gm',ocr=False,blank_pages_list=blank_pages)
    with library.db() as db:db.execute('UPDATE documents SET status="extracted",pages=?,quality=? WHERE id=?',(count,json.dumps(quality),sha))

def import_archive(library,path,bundle,purpose,parent,job,budget,depth=0):
    if depth>2:raise ValueError('Слишком глубокий архив')
    with zipfile.ZipFile(path) as zipped:
        files=[]
        for entry in zipped.infolist():
            if entry.is_dir():continue
            name=zip_name(entry);suffix=Path(name).suffix.lower()
            if suffix not in FORMATS or entry.flag_bits & 1:raise ValueError('Недопустимое или зашифрованное вложение')
            if entry.file_size>MAX_FILE or entry.file_size/max(1,entry.compress_size)>200:raise ValueError('ZIP превышает предел распаковки')
            budget['files']+=1;budget['bytes']+=entry.file_size
            if budget['files']>MAX_FILES or budget['bytes']>MAX_PACKAGE:raise ValueError('Лимит файлов/размера комплекта')
            files.append((entry,name,suffix))
        if not files:raise ValueError('Пустой архив')
        for index,(entry,name,suffix) in enumerate(files):
            with tempfile.TemporaryDirectory(dir=library.files) as temporary:
                target=Path(temporary)/'attachment.bin'
                with zipped.open(entry) as source,target.open('wb') as dest:
                    actual=0
                    while chunk:=source.read(1024*1024):
                        actual+=len(chunk)
                        if actual>entry.file_size or actual>MAX_FILE:raise ValueError('Размер вложения не совпадает')
                        dest.write(chunk)
                if actual!=entry.file_size:raise ValueError('Повреждённое вложение')
                sha=library.put_file(target)
                with library.db() as db:
                    db.execute('INSERT OR IGNORE INTO documents(id,format,bytes) VALUES(?,?,?)',(sha,suffix,actual))
                    db.execute('INSERT OR IGNORE INTO bundle_files VALUES(?,?,?,?,?)',(bundle,sha,name,purpose,parent))
                if suffix=='.zip':import_archive(library,library.blob(sha),bundle,purpose,sha,job,budget,depth+1)
                else:extract_document(library,sha,suffix,purpose,None)
            if job:library.progress(job,done=index+1,total=len(files))
    with library.db() as db:db.execute('UPDATE documents SET status="extracted",quality=? WHERE id=?',(json.dumps({'archive_files':len(files),'crc':'verified_during_full_read','mechanics':'requires_review'}),parent))

def auto_split_book(library,record,format):
    """A whole book a player uploaded gets its page ranges proposed, nothing more.

    Only a bundle the preparation dialog created is touched (`auto_split` in its
    metadata): a bundle someone assembles by hand keeps the reviewed split path,
    where a person confirms every boundary. Nothing here invents events, tables
    or instructions out of prose; the module is a page range and a title.
    """
    if record['purpose']!='book' or format not in ('.pdf','.txt','.md'):return None
    metadata=library.get(record['bundle'])['metadata']
    if not metadata.get('auto_split'):return None
    edition=str(metadata.get('edition','')).lower().replace('dnd','')
    return library.auto_split(record['bundle'],record['document'],confirm=True,ruleset='dnd'+edition if edition in ('2014','2024') else 'dnd2024')

def run(data,job):
    library=AdventureLibrary(data);record=library.job(job)
    with library.db() as db:
        if db.execute('UPDATE jobs SET status="running",worker_pid=?,message=? WHERE id=? AND status="queued"',(os.getpid(),'Локальное извлечение. Сценарные инструкции требуют проверки.',job)).rowcount!=1:return
    try:
        with library.db() as db:format=db.execute('SELECT format FROM documents WHERE id=?',(record['document'],)).fetchone()[0]
        if format=='.zip':import_archive(library,library.blob(record['document']),record['bundle'],record['purpose'],record['document'],job,{'files':0,'bytes':0})
        else:extract_document(library,record['document'],format,record['purpose'],job)
        with library.db() as db:characters=db.execute('SELECT coalesce(sum(length(content)),0) FROM pages WHERE document=?',(record['document'],)).fetchone()[0]
        message='Извлечено знаков: '+str(characters)+'. Подготовка правил, событий и доступа ещё требует проверки.'
        try:summary=auto_split_book(library,record,format)
        except ValueError as error:summary=None;message='Извлечено знаков: '+str(characters)+'. Нарезка модулей не выполнена: '+str(error)
        if summary:
            message='Извлечено страниц: '+str(summary['pages'])+', знаков: '+str(characters)+'. Модулей: '+str(len(summary['modules']))+'.'
            if summary['note']:message+=' '+summary['note']+'.'
            message+=' Игровые инструкции по-прежнему требуют проверки.'
        library.progress(job,status='complete',message=message)
    except BaseException as error:
        # Причина — в сообщении, а не только в классе ошибки: «Извлечение не
        # завершено (ValueError)» ничего не говорит человеку о том, что делать.
        reason=str(error).strip() or type(error).__name__
        library.progress(job,status='failed',message='Извлечение не завершено: '+reason[:600]+
            ' Оригинал сохранён; непроверенные материалы не готовы к игре.')
        raise

if __name__=='__main__':
    if sys.argv[1]=='--render':render_material(AdventureLibrary(sys.argv[2]),sys.argv[3],int(sys.argv[4]))
    else:run(sys.argv[1],sys.argv[2])
