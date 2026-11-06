"""Generic local PDF layout index. Offsets reference pages.content; no text copy.

Geometry is in PDF points, bottom-left origin. Layout hints are candidates,
never semantic truth, read-aloud approval, map topology or executable rules.
"""
import hashlib,json,re
from .adventure_library import digest

VERSION=1
MAX_PAGE_CHARS=500000
KINDS=('text','table','map','image','handout','npc','opponent','location','event','reward','additional_rule')

def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS pdf_layout(document TEXT NOT NULL,page INTEGER NOT NULL,version INTEGER NOT NULL,text_sha TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(document,page),FOREIGN KEY(document,page) REFERENCES pages(document,number) ON DELETE CASCADE)')
    db.execute("CREATE INDEX IF NOT EXISTS analysis_cache_document_mode ON analysis_cache(json_extract(body,'$.identity.sha256'),json_extract(body,'$.mode'))")

def text_sha(text):return hashlib.sha256(text.encode()).hexdigest()

def layout_page(page,textpage,text,number):
    w,h=page.get_size();regions=[];cursor=0;pdf_cursor=0;missing=0
    for line in text.splitlines(keepends=True):
        start=cursor;cursor+=len(line);value=line.rstrip('\r\n')
        if not value.strip():continue
        bounds=[];search=None
        try:
            search=textpage.search(value.strip(),index=pdf_cursor,match_case=True)
            found=search.get_next()
            if found:
                index,count=found;pdf_cursor=index+count
                bounds=[list(textpage.get_rect(i)) for i in range(textpage.count_rects(index,count))]
        except (ValueError,RuntimeError):pass
        finally:
            if search:search.close()
        if not bounds:missing+=1
        regions.append(dict(id=f'p{number}.t{start}-{start+len(value)}',start=start,end=start+len(value),bounds=bounds))
    images=[];images_truncated=False
    import pypdfium2 as pdfium
    for i,obj in enumerate(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE],max_depth=4)):
        if i>=100:images_truncated=True;break
        try:images.append(dict(id=f'p{number}.i{i}',bounds=list(obj.get_bounds())))
        except (ValueError,RuntimeError):pass
    hints=[]
    patterns={'table':r'^(?:таблица|table)\b|^\d+\s*[-–—]\s*\d+\s+','map':r'^(?:карта|map)\b','handout':r'^(?:раздат|handout)',
        'npc':r'^(?:важные персонажи|неигровые персонажи|npc)\b','opponent':r'^(?:противники|монстры|враги|monsters)\b',
        'event':r'^(?:события|столкновения|events|encounters)\b','reward':r'^(?:награды|сокровища|treasure|rewards)\b',
        'additional_rule':r'^(?:дополнительные правила|вариант правила|optional rule|variant rule)'}
    for region in regions:
        value=text[region['start']:region['end']].strip()
        for kind,pattern in patterns.items():
            if re.search(pattern,value,re.I):hints.append(dict(kind=kind,region=region['id'],status='requires_review'))
    return dict(version=VERSION,coordinates='pdf_points_bottom_left',width=w,height=h,rotation=page.get_rotation(),render_ref=f'p{number}.render',
        regions=regions,images=images,images_truncated=images_truncated,hints=hints,needs_ocr=not bool(text.strip()),geometry_missing=missing,semantic_status='requires_review')

def save_layout(db,document,page,text,layout):
    schema(db)
    db.execute('INSERT INTO pdf_layout VALUES(?,?,?,?,?) ON CONFLICT(document,page) DO UPDATE SET version=excluded.version,text_sha=excluded.text_sha,body=excluded.body',(document,page,VERSION,text_sha(text),json.dumps(layout,separators=(',',':'))))

class PDFPipeline:
    def __init__(self,library):
        self.library=library
        with library.db() as db:schema(db)
    def index(self,document,pages=None):
        path=self.library.blob(document)
        with self.library.db() as db:
            row=db.execute('SELECT format,status FROM documents WHERE id=?',(document,)).fetchone()
        if not row or row['format']!='.pdf' or row['status']!='extracted':raise ValueError('Сначала извлеките PDF локальным импортом')
        if digest(path)!=document:raise ValueError('Контрольная сумма исходного PDF изменилась')
        import pypdfium2 as pdfium
        errors=[];indexed=0;cached=0
        with pdfium.PdfDocument(path) as pdf:
            selected=sorted(set(pages if pages is not None else range(1,len(pdf)+1)))
            if not selected or any(type(p) is not int or not 1<=p<=len(pdf) for p in selected):raise ValueError('Страница вне исходного PDF')
            for number in selected:
                with self.library.db() as db:
                    row=db.execute('SELECT content FROM pages WHERE document=? AND number=?',(document,number)).fetchone()
                    old=db.execute('SELECT version,text_sha FROM pdf_layout WHERE document=? AND page=?',(document,number)).fetchone()
                if row and old and old['version']==VERSION and old['text_sha']==text_sha(row['content']):cached+=1;continue
                page=pdf[number-1]
                try:
                    tp=page.get_textpage()
                    try:
                        text=tp.get_text_range() or ''
                        if len(text)>MAX_PAGE_CHARS:raise ValueError('Текст страницы превышает безопасный лимит; данные не обрезаны')
                        layout=layout_page(page,tp,text,number)
                    finally:tp.close()
                    with self.library.db() as db:
                        if not row:raise ValueError('Страница отсутствует в локальном извлечении')
                        if text!=row['content']:raise ValueError('Локальный текст неполон или отличается от PDF; требуется повторное извлечение этой страницы')
                        save_layout(db,document,number,text,layout)
                    indexed+=1
                except (ValueError,RuntimeError) as exc:errors.append(dict(page=number,reason=str(exc)))
                finally:page.close()
        return dict(document=document,indexed=indexed,cached=cached,errors=errors,semantic_status='requires_review')
    def page(self,document,number):
        with self.library.db() as db:
            row=db.execute('SELECT l.*,p.content FROM pdf_layout l JOIN pages p ON p.document=l.document AND p.number=l.page WHERE l.document=? AND l.page=?',(document,number)).fetchone()
        if not row or row['version']!=VERSION or row['text_sha']!=text_sha(row['content']):raise ValueError('Индекс страницы отсутствует или устарел')
        return json.loads(row['body'])
    def summary(self,document):
        from collections import Counter
        counts=Counter();regions=0;images=0;ocr=[];stale=[]
        with self.library.db() as db:
            rows=db.execute('SELECT l.page,l.body,l.version,l.text_sha,p.content FROM pdf_layout l JOIN pages p ON p.document=l.document AND p.number=l.page WHERE l.document=? ORDER BY l.page',(document,)).fetchall()
        for row in rows:
            if row['version']!=VERSION or row['text_sha']!=text_sha(row['content']):stale.append(row['page']);continue
            value=json.loads(row['body']);regions+=len(value['regions']);images+=len(value['images'])
            counts.update(h['kind'] for h in value['hints'])
            if value['needs_ocr']:ocr.append(row['page'])
        return dict(document=document,indexed_pages=len(rows)-len(stale),stale_pages=stale,text_regions=regions,image_regions=images,hints=dict(counts),ocr_pages=ocr,semantic_status='requires_review')
