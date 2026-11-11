"""Bounded import/render worker, isolated from the interactive backend process."""
import io,json,os,re,stat,sys,unicodedata,zipfile,tempfile
from pathlib import Path,PurePosixPath
from .adventure_library import AdventureLibrary,MAX_FILE,MAX_PACKAGE,MAX_FILES,FORMATS,file_size,stable

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
    output=library.files/(material['document']+'-p'+str(material['page'])+'-w'+str(width)+suffix)
    if output.exists():return output
    from PIL import Image,ImageOps
    Image.MAX_IMAGE_PIXELS=16777216
    if document['format']=='.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(library.blob(material['document'])) as pdf:
            page=pdf[material['page']-1]
            try:
                w,h=page.get_size();scale=min(width/max(w,h),4096/max(w,h))
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

#: Encodings a Russian text file realistically arrives in. Only UTF-8 and
#: windows-1251 used to be tried, so a cp866 or KOI8-R book became mojibake that
#: then looked like a model failure; and a byte outside both code pages made the
#: import fail with a bare UnicodeDecodeError instead of telling the player why.
TEXT_ENCODINGS=('utf-8-sig','cp1251','cp866','koi8-r')
#: Предел текстового источника; такой файл уже не книга, а скорее свалка.
TEXT_LIMIT=12000000
#: Frequent Russian letter pairs. Correct text has many of them, wrong-code-page
#: text has almost none, so the same decoding cannot win by accident.
RUSSIAN_BIGRAMS=('ст','но','то','на','ен','ов','ни','ра','ко','по','пр','ро','ос','го','ер','ал',
    'ли','ре','ка','та','ет','ла','ит','за','чт','ль','ол','де','ат','ьн','ый','ой','ам','ах','ом',
    'ег','ск','ва','не','чн','жд','ут','ад','ел','ест','ени','ого')

def text_score(text):
    """How much a decoding reads like prose: letters plus Russian letter pairs."""
    length=max(1,len(text))
    letters=sum(character.isalpha() for character in text)
    broken=sum(1 for character in text if unicodedata.category(character) in ('Cc','Cs','Co','Cf')
               and character not in '\n\r\t')+text.count('\ufffd')
    lower=text.lower();bigrams=sum(lower.count(pair) for pair in RUSSIAN_BIGRAMS)
    return letters/length+3*bigrams/length-20*broken/length

def decode_text(raw):
    """Decode a text source into (text, encoding); never return mojibake silently."""
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
    return text,encoding

def human_reason(error):
    """Причина для человека: наша русская фраза, а не имя класса.

    Раньше `run()` писал в журнал только `(ValueError)`: игрок видел «не удалось»
    без причины, хотя код уже знал, что в PDF нет текстового слоя. Английский
    текст исключения (внутренности библиотек, пути) наружу не отдаём.
    """
    message=str(error)
    return message if isinstance(error,ValueError) and re.search(r'[А-Яа-яЁё]',message) else ''

def discard_document(library,sha,reason,quality=None):
    """Drop rows and rendered pages of a source that produced nothing usable.

    A refused file must not leave page rows, materials or candidates behind: the
    library would show a page nobody can read as if it were a usable source.
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
    if re.match(r'\s*(глава|эпизод|приложение|chapter)\b',line,re.I):return 'chapter'
    if re.match(r'\s*(таблица|table)\b',line,re.I):return 'table'
    if re.match(r'\s*(раздат|handout|карта|map)\b',line,re.I):return 'media'
    if re.match(r'\s*(если|через|каждый|каждую)\b',line,re.I):return 'instruction'
    return None

def extract_document(library,sha,format,purpose,job):
    with library.db() as db:
        previous=db.execute('SELECT status FROM documents WHERE id=?',(sha,)).fetchone()
        if previous and previous[0]=='extracted':return
        db.execute('UPDATE documents SET status="extracting" WHERE id=?',(sha,))
    path=library.blob(sha);total_text=0;blank=0;partial=[];blank_pages=[];pictured_pages=[];total_letters=0;text_encoding=None
    if format=='.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(path) as pdf:
            count=len(pdf)
            if not 1<=count<=2000:raise ValueError('Лимит страниц PDF')
            if job:library.progress(job,total=count)
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
                    total_text+=len(text)
                    total_letters+=len(re.sub(r'\W','',text))
                    if total_text>12000000:raise ValueError('Лимит извлечённого текста')
                    if len(re.sub(r'\W','',text))<30:
                        # Remember which pages carry no words: a page with a map
                        # on it explains itself, a silent page does not.
                        blank_pages.append(index+1)
                        blank+=1
                    images=[image['bounds'] for image in layout['images']]
                    if images:pictured_pages.append(index+1)
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
                    for line in text.splitlines():
                        line=line.strip()[:180];kind_hint=candidate_kind(line)
                        if kind_hint:
                            cid=stable(sha,index+1,kind_hint,line)
                            db.execute('INSERT OR IGNORE INTO candidates(id,document,page,kind,title,body) VALUES(?,?,?,?,?,?)',(cid,sha,index+1,kind_hint,line,json.dumps({'scope':'gm','confidence':'candidate','printed_page':label})))
                # Render all standalone map/handout PDF pages, rather than counting filenames.
                if purpose in ('maps','handouts') or index==0:render_material(library,mid,480)
                if job:library.progress(job,done=index+1)
        if purpose=='book' and total_letters<30:
            # Every page was rendered and none carried words: this is a scan. OCR is
            # out of scope, and a source that reaches the model as zero characters is
            # worse than an honest refusal. Clean the rows so nothing unusable stays
            # in the library, then say what file would work. Maps and handouts are
            # exempt: a wordless map page is the whole point of them.
            reason=('В этом PDF нет текстового слоя: распознавание (OCR) не поддерживается. '
                    'Нужен PDF с текстом или TXT.')
            discard_document(library,sha,reason,dict(text_characters=0,blank_pages=blank,
                blank_pages_list=blank_pages,pictured_pages=pictured_pages,ocr=False,text_encoding=None))
            raise ValueError(reason)
    elif format in ('.png','.jpg','.jpeg','.webp','.svg'):
        count=1;mid=stable(sha,1)
        with library.db() as db:db.execute('INSERT OR IGNORE INTO materials(id,document,page,kind) VALUES(?,?,?,?)',(mid,sha,1,'map' if purpose=='maps' else 'handout' if purpose=='handouts' else 'illustration'))
        render_material(library,mid,480)
    else:
        size=path.stat().st_size
        if size>TEXT_LIMIT:raise ValueError('Текстовый файл '+file_size(size)+' — предел '+file_size(TEXT_LIMIT)+'; разделите текст на части. Оригинал на вашем диске не изменён.')
        raw=path.read_bytes()
        # Кодировку выбирает decode_text по содержанию: cp1251 без проверки
        # молча превращал cp866 и KOI8-R в кракозябры, которые потом выглядели
        # как отказ модели.
        text,encoding=decode_text(raw);text_encoding=encoding
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
    quality=dict(text_characters=total_text,blank_pages=blank,partial_pages=partial,mechanics='requires_review',media_scope='gm',ocr=False,
                 blank_pages_list=blank_pages,pictured_pages=pictured_pages,text_encoding=text_encoding)
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

def extraction_summary(library,sha):
    """Что именно извлечено — числом, чтобы журнал не ограничивался «готово»."""
    with library.db() as db:
        row=db.execute('SELECT pages,quality FROM documents WHERE id=?',(sha,)).fetchone()
    if row is None:return 'документ не найден'
    quality=json.loads(row['quality'] or '{}')
    characters=int(quality.get('text_characters') or 0)
    pages=int(row['pages'] or 0)
    detail=('страница' if pages==1 else 'страниц') if pages<5 else 'страниц'
    return (str(pages)+' '+detail+', '+('{:,}'.format(characters)).replace(',',' ')+' символов')

def run(data,job):
    library=AdventureLibrary(data);record=library.job(job)
    with library.db() as db:
        if db.execute('UPDATE jobs SET status="running",worker_pid=?,message=? WHERE id=? AND status="queued"',(os.getpid(),'Локальное извлечение. Сценарные инструкции требуют проверки.',job)).rowcount!=1:return
    try:
        with library.db() as db:format=db.execute('SELECT format FROM documents WHERE id=?',(record['document'],)).fetchone()[0]
        if format=='.zip':import_archive(library,library.blob(record['document']),record['bundle'],record['purpose'],record['document'],job,{'files':0,'bytes':0})
        else:extract_document(library,record['document'],format,record['purpose'],job)
        library.progress(job,status='complete',message='Материалы извлечены: '+extraction_summary(library,record['document'])+
            '. Подготовка правил, событий и доступа ещё требует проверки.')
    except BaseException as error:
        reason=human_reason(error)
        # Причина остаётся в журнале: без неё игрок видел «не удалось извлечь»
        # и не знал, что дело в скане без текстового слоя.
        message=(('Извлечение не завершено: '+reason) if reason else
                 'Извлечение не завершено ('+type(error).__name__+'). Оригинал сохранён; непроверенные материалы не готовы к игре.')
        library.progress(job,status='failed',message=message)
        raise

if __name__=='__main__':
    if sys.argv[1]=='--render':render_material(AdventureLibrary(sys.argv[2]),sys.argv[3],int(sys.argv[4]))
    else:run(sys.argv[1],sys.argv[2])
