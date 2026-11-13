"""Private, content-addressed adventure library; originals never enter installed assets."""
from contextlib import contextmanager
import hashlib, json, os, re, sqlite3, subprocess, sys, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MAX_FILE=384*1024*1024
MAX_PACKAGE=768*1024*1024
MAX_FILES=300
#: A page is drawn in its own process, so a runaway picture costs one preview and
#: not the backend: the child is killed once it outgrows the import budget.
MAX_RENDER_RSS=768*1024*1024
RENDER_SECONDS=35
#: Two previews at once is what a two-core machine carries; the rest wait.
MAX_RENDERS=2
RENDER_WAIT=20
FORMATS={'.pdf','.txt','.md','.json','.zip','.png','.jpg','.jpeg','.webp','.svg'}

def stable(*values):
    return hashlib.sha256(json.dumps(values,ensure_ascii=False).encode()).hexdigest()[:32]

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

#: A bundle the preparation dialog filled in is read as a whole book, so its page
#: ranges may be proposed automatically. A bundle a person assembles by hand keeps
#: the reviewed split path, where every boundary is confirmed deliberately.
AUTO_SPLIT_KEY='auto_split'
MIN_MODULE_PAGES=3
MAX_AUTO_MODULES=60
#: A running head repeats for as long as its chapter lasts, so a heading printed
#: on this many pages already says the chapter lives where it stands.
HEADER_PAGES=3
#: A heading printed once may still open a chapter — a two-page appendix does —
#: but then the page itself has to carry text: a cover, a title page, a colophon
#: and an empty page print less than this much prose.
CONTENT_CHARS=600
#: A contents page names several chapters in a row («Эпизод 1 … 17», «Эпизод 2 …
#: 23»), a chapter's own first page never does. This many different headings on
#: one page read as contents.
CONTENTS_HEADS=3
#: A printed line at least this long reads as prose; a heading, a folio and a
#: table-of-contents line stay below it.
PROSE_LINE=40

def chapter_key(title):
    """Which chapter a printed line names, or None when it names no chapter.

    A running header repeats on every page of its chapter and often carries a page
    number, so counting distinct titles would cut a ten-chapter book into two
    hundred one-page modules. What identifies the chapter is the number in
    «Глава 3» / «Chapter 3» or the code in «DDEX1-2». An appendix is numbered by a
    letter, and its running head prints that letter after a bar
    («Приложение А | Магические предметы 209») while the heading itself stands
    alone («ПРИЛОЖЕНИЕ А») — both name appendix A. Three printed shapes must not
    be mistaken for a heading: a table-of-contents line (dot leaders), a wrapped
    sentence that merely starts with the word «глава», and a prose mention of a
    chapter number — the last one is why `chapter_starts` also weighs repetition.
    """
    text=(title or '').strip();low=text.lower()
    if not text or len(text)>90 or re.search(r'\.{3,}',text):return None
    code=re.search(r'\b([a-z]{2,6}\d{0,2}-\d{1,3})\b',low)
    if code:return ('code',code[1])
    match=re.match(r'(глава|chapter|эпизод|episode|часть|part)\s*(\d{1,3})\b',low)
    if match:return (match[1],match[2])
    # A sentence that happens to begin with the word continues after the letter
    # («приложение А). Персонаж вновь сможет…», «приложение С)»), so a handbook of
    # items would otherwise open an appendix on every page that mentions one. The
    # letter has to be followed by a separator, a printed page number or the end
    # of the line: a credits line («Приложение А - korfax, Kestel») carries a dash
    # and is not a heading either.
    match=re.match(r'(приложение|appendix)\s+([a-zа-я])(?=\s*(?:[|:.]|\d{1,4}\s*$|$))',low)
    if match:return (match[1],match[2])
    return None

#: Chapter kinds that are reference material rather than an episode: a book's
#: appendices print items, monsters, maps and dramatis personae, and are never a
#: place a campaign can start. They stay readable and their places travel to the
#: world, but they are not counted as adventures.
REFERENCE_KINDS={'приложение','appendix'}

def is_reference(title):
    """Whether a printed heading names an appendix of the book."""
    key=chapter_key(title)
    return bool(key and key[0] in REFERENCE_KINDS)

def prose_chars(text):
    """How much running text a page prints, without headings, folios and leaders."""
    return sum(len(line.strip()) for line in str(text or '').splitlines() if len(line.strip())>=PROSE_LINE)

def opens_chapter(page,printed_pages,texts=None,heads=None):
    """Whether the page a heading stands on opens a chapter or only mentions one.

    Three printed pages of the same head already prove the chapter lives there: a
    running head repeats as long as its chapter lasts, so the longest run of
    near-consecutive pages is the chapter itself. A heading printed once proves
    nothing on its own — an appendix of two pages is printed once, and so is a
    table-of-contents line, which is what cut The Rise of Tiamat into a first
    module made of its own contents pages. A single-printed heading therefore
    needs the page to carry content of its own: `texts` holds what each page
    prints, and a page naming several different chapters in a row is read as
    contents whatever its line length (a contents page is long and still holds
    no playable text). When the page text was not read at all, nothing is
    claimed about it and the heading is kept, as it was before.
    """
    if len(set(printed_pages))>=HEADER_PAGES:return True
    if not texts or page not in texts:return True
    if len((heads or {}).get(page,()))>=CONTENTS_HEADS:return False
    return prose_chars(texts[page])>=CONTENT_CHARS

def chapter_starts(rows,texts=None):
    """Where each chapter really begins: the first page of its longest header run.

    A chapter's header repeats for as long as the chapter lasts, while a sentence
    like «Глава 7 описывает сокровища» appears once. Taking the first page of the
    longest run of near-consecutive pages therefore finds the chapter, not the
    mention of it. Pages are compared with a two-page tolerance: a chapter spread
    or a page without text must not cut the run in half.

    The title is the form printed most often on the chapter's own pages. When two
    forms tie, the one that reads as a printed header wins: a credits line names
    the translators who worked on the episode («Эпизод 5 - LeoTrickster, stivie»)
    and a table caption carries a dash, while a header carries a colon or a full
    stop. Header-ness must come first: a credits line printed on the last page of
    a run otherwise wins the tie. Among headers the cleaner printed form wins (a
    two-column extraction leaves «Глава 1 | Начало» beside «Глава 1. Начало»), and
    the last page of the run only settles a remaining tie.

    `texts` maps a page number to what that page prints. It is what separates a
    chapter's first page from a contents page that names it: see `opens_chapter`.
    """
    pages={};forms={};heads={}
    for page,kind,title in rows:
        key=chapter_key(title)
        if not key:continue
        printed=re.sub(r'\s+\d{1,4}$','',(title or '').strip())[:150]
        pages.setdefault(key,[]).append(int(page))
        forms.setdefault(key,{}).setdefault(printed,set()).add(int(page))
        heads.setdefault(int(page),set()).add(key)
    starts=[]
    for key,found in pages.items():
        found=sorted(set(found));runs=[];run=[found[0]]
        for page in found[1:]:
            if page-run[-1]<=2:run.append(page)
            else:runs.append(run);run=[page]
        runs.append(run)
        # The longest run whose first page really opens a chapter. A chapter named
        # by a contents page is found on the page that prints it too, so a run cut
        # by the contents page is not the chapter and the next run is tried; only a
        # run that opens nowhere leaves the heading without a boundary.
        best=next((run for run in sorted(runs,key=lambda run:(-len(run),run[0])) if opens_chapter(run[0],found,texts,heads)),None)
        if best is None:continue
        # The form printed most often on the run's own pages is the header; a
        # table caption inside the chapter («Таблица: Эпизод 3 - Столкновения»)
        # and a credits line both name the same chapter number, so the shortest
        # form alone would pick whichever happens to be shorter.
        # Header first, then the cleanest printed form (a two-column extraction can
        # leave «Глава 1 | Начало» beside «Глава 1. Начало»), then the last page of
        # the run, then the shortest. When the run holds both forms equally often,
        # the one the book prints on more of its pages wins: an appendix lasts five
        # pages but its head appears on two of them and on a bare heading, and
        # «ПРИЛОЖЕНИЕ E» alone is a worse name for a module than
        # «Приложение E | Реплики Глоссофобия».
        on_pages=[(len(where & set(best)), len(where), not re.search(r',|\s[-–—]\s', text), -len(text), best[-1] in where, text)
                  for text, where in forms[key].items() if where & set(best)]
        if on_pages: starts.append((best[0], max(on_pages)[-1]))
        else: starts.append((best[0], min(forms[key], key=len)))
    starts.sort()
    return starts

def module_spans(starts,pages,minimum=MIN_MODULE_PAGES,limit=MAX_AUTO_MODULES):
    """Non-overlapping page ranges; a chapter shorter than `minimum` joins the next.

    Pages before the first heading stay out of the spans on purpose: a cover,
    credits and a table of contents are not a playable module. `auto_split` gives
    them to the first module as its second range, so they stay readable as
    material without becoming an episode. An empty list means the headings cannot
    serve as boundaries at all.
    """
    points=[]
    for page,title in starts:
        if 1<=page<=pages and (not points or page>points[-1][0]):points.append((page,title))
    if not points or len(points)>limit:return []
    spans=[]
    for index,(page,title) in enumerate(points):
        spans.append([page,points[index+1][0]-1 if index+1<len(points) else pages,title])
    merged=[]
    for span in spans:
        # A short chapter joins the next one instead of becoming its own module:
        # the range still starts where the book starts, so no page is dropped.
        if merged and merged[-1][1]-merged[-1][0]+1<minimum:merged[-1][1]=span[1]
        else:merged.append(span)
    if len(merged)>1 and merged[-1][1]-merged[-1][0]+1<minimum:merged[-2][1]=merged[-1][1];merged.pop()
    return [tuple(span) for span in merged]

class AdventureLibrary:
    def __init__(self,data):
        self.data=Path(data).resolve();self.path=self.data/'adventure.sqlite3';self.files=self.data/'adventure-files'
        self.files.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS bundles(id TEXT PRIMARY KEY,title TEXT NOT NULL,metadata TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,format TEXT NOT NULL,bytes INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',pages INTEGER NOT NULL DEFAULT 0,quality TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS bundle_files(bundle TEXT NOT NULL REFERENCES bundles(id),document TEXT NOT NULL REFERENCES documents(id),original_name TEXT NOT NULL,purpose TEXT NOT NULL,parent TEXT NOT NULL DEFAULT '',PRIMARY KEY(bundle,document,original_name));
            CREATE TABLE IF NOT EXISTS pages(id INTEGER PRIMARY KEY,document TEXT NOT NULL REFERENCES documents(id),number INTEGER NOT NULL,label TEXT NOT NULL DEFAULT '',content TEXT NOT NULL,objects TEXT NOT NULL DEFAULT '[]',UNIQUE(document,number));
            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(content,content='pages',content_rowid='id',tokenize='unicode61');
            CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY,document TEXT NOT NULL REFERENCES documents(id),page INTEGER NOT NULL,kind TEXT NOT NULL,scope TEXT NOT NULL DEFAULT 'gm',public_name TEXT NOT NULL DEFAULT '',reviewed INTEGER NOT NULL DEFAULT 0,sanitized INTEGER NOT NULL DEFAULT 0,geometry TEXT,variant TEXT NOT NULL DEFAULT '',region TEXT NOT NULL DEFAULT '',bounds TEXT,caption TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'page',markup TEXT NOT NULL DEFAULT '',markup_state TEXT NOT NULL DEFAULT '',markup_note TEXT NOT NULL DEFAULT '',UNIQUE(document,page,region));
            CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,document TEXT NOT NULL REFERENCES documents(id),page INTEGER NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'requires_review');
            CREATE TABLE IF NOT EXISTS definitions(bundle TEXT PRIMARY KEY REFERENCES bundles(id),body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS adventure_modules(bundle TEXT NOT NULL REFERENCES bundles(id),id TEXT NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(bundle,id));
            CREATE TABLE IF NOT EXISTS module_scenes(bundle TEXT NOT NULL REFERENCES bundles(id),module TEXT NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(bundle,module));
            CREATE TABLE IF NOT EXISTS scene_edits(bundle TEXT NOT NULL REFERENCES bundles(id),document TEXT NOT NULL,page INTEGER NOT NULL,entity TEXT NOT NULL,name_key TEXT NOT NULL,name TEXT NOT NULL DEFAULT '',value TEXT NOT NULL,created REAL NOT NULL DEFAULT 0,PRIMARY KEY(bundle,document,page,entity,name_key));
            CREATE TABLE IF NOT EXISTS analysis_cache(key TEXT PRIMARY KEY,body TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,bundle TEXT NOT NULL REFERENCES bundles(id),document TEXT NOT NULL REFERENCES documents(id),original_name TEXT NOT NULL,purpose TEXT NOT NULL,status TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT '',created REAL NOT NULL);
            PRAGMA user_version=1;''')
            if 'worker_pid' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:db.execute('ALTER TABLE jobs ADD COLUMN worker_pid INTEGER')
            if 'public_text' not in {row[1] for row in db.execute('PRAGMA table_info(materials)')}:db.execute('ALTER TABLE materials ADD COLUMN public_text TEXT NOT NULL DEFAULT ""')
            # A page used to hold at most one material. A picture cut out of a page is
            # a second material on the same page, so a material is now named by
            # (document,page,region). An existing library is rebuilt once, keeping
            # every row: its page materials stay page materials.
            if 'region' not in {row[1] for row in db.execute('PRAGMA table_info(materials)')}:
                db.execute('ALTER TABLE materials RENAME TO materials_by_page')
                db.execute('''CREATE TABLE materials(id TEXT PRIMARY KEY,document TEXT NOT NULL REFERENCES documents(id),page INTEGER NOT NULL,kind TEXT NOT NULL,scope TEXT NOT NULL DEFAULT 'gm',public_name TEXT NOT NULL DEFAULT '',reviewed INTEGER NOT NULL DEFAULT 0,sanitized INTEGER NOT NULL DEFAULT 0,geometry TEXT,variant TEXT NOT NULL DEFAULT '',public_text TEXT NOT NULL DEFAULT '',region TEXT NOT NULL DEFAULT '',bounds TEXT,caption TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'page',markup TEXT NOT NULL DEFAULT '',markup_state TEXT NOT NULL DEFAULT '',markup_note TEXT NOT NULL DEFAULT '',UNIQUE(document,page,region))''')
                db.execute('''INSERT INTO materials(id,document,page,kind,scope,public_name,reviewed,sanitized,geometry,variant,public_text,region,bounds,caption,status,markup,markup_state,markup_note) SELECT id,document,page,kind,scope,public_name,reviewed,sanitized,geometry,variant,public_text,'',NULL,'',CASE WHEN reviewed=1 THEN 'confirmed' ELSE 'page' END,'','','' FROM materials_by_page''')
                db.execute('DROP TABLE materials_by_page')
            # What the picture shows, in words a player can describe it with: the
            # model's markup of one material, never a fact about the book.
            columns={row[1] for row in db.execute('PRAGMA table_info(materials)')}
            for name in ('markup','markup_state','markup_note'):
                if name not in columns:db.execute('ALTER TABLE materials ADD COLUMN '+name+' TEXT NOT NULL DEFAULT ""')
            for row in db.execute('SELECT bundle,body,revision FROM definitions').fetchall():
                value=json.loads(row['body']);db.execute('INSERT OR IGNORE INTO adventure_modules VALUES(?,?,?,?)',(row['bundle'],value['id'],row['body'],row['revision']))
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=20);db.row_factory=sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA foreign_keys=ON')
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()
    def blob(self,id):
        if not re.fullmatch(r'[0-9a-f]{64}',id):raise ValueError('Некорректный источник')
        path=self.files/(id+'.bin')
        if path.is_symlink():raise ValueError('Источник недоступен')
        return path
    def put_file(self,path):
        path=Path(path);sha=digest(path);dest=self.blob(sha)
        if not dest.exists():
            import shutil
            temporary=self.files/(sha+'.tmp-'+uuid.uuid4().hex);shutil.copyfile(path,temporary);temporary.replace(dest)
        elif digest(dest)!=sha:raise ValueError('Контрольная сумма локального источника не совпадает')
        return sha
    def create(self,title,metadata=None):
        if not isinstance(title,str) or not 1<=len(title.strip())<=150:raise ValueError('Укажите название комплекта')
        metadata=metadata or {}
        if len(json.dumps(metadata))>12000:raise ValueError('Метаданные слишком велики')
        id=str(uuid.uuid4())
        with self.db() as db:db.execute('INSERT INTO bundles VALUES(?,?,?,0,?)',(id,title.strip(),json.dumps(metadata,ensure_ascii=False),time.time()))
        return self.get(id)
    def update_metadata(self,id,title,metadata):
        self.get(id)
        if not 1<=len(title.strip())<=150 or len(json.dumps(metadata))>12000:raise ValueError('Некорректные метаданные')
        with self.db() as db:db.execute('UPDATE bundles SET title=?,metadata=?,revision=revision+1 WHERE id=?',(title.strip(),json.dumps(metadata,ensure_ascii=False),id))
        return self.get(id)
    def readiness(self,id):
        """Единое состояние книги: шаги, счётчики одной ревизии, предупреждения."""
        from .book_readiness import readiness
        return readiness(self,id)
    def get(self,id,modules=None):
        # The card answers with one aggregate: the module count, the pages, the
        # status word and the two readiness flags come from the same snapshot of
        # the database. The parameter is kept for callers that already know the
        # count, but the number is asked of the table at answer time — a person
        # splits, re-splits and deletes modules after the import, and the job
        # message keeps the sentence written when the book was read.
        from .book_readiness import summary
        report=self.readiness(id)
        counts=report['counts']
        return dict(id=id,title=report['title'],metadata=report['metadata'],revision=report['revision'],
                    documents=counts['documents'],pages=counts['pages'],modules=counts['modules'],
                    status=report['status'],jobs=report['jobs'],ruleset=report['ruleset'],
                    ruleset_version=report['ruleset_version'],import_complete=report['import_complete'],
                    readiness=summary(report))
    def list(self):
        # One answer, one source of numbers: each card is the same aggregate the
        # bundle screen shows. A second grouped count here is exactly how a card
        # ended up saying «12 модулей» over a list of eleven.
        with self.db() as db:
            ids=[r[0] for r in db.execute('SELECT id FROM bundles ORDER BY created DESC')]
        return [self.get(id) for id in ids]
    def job(self,id):
        with self.db() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
            if not row:raise KeyError('Импорт не найден')
            return dict(row)
    def progress(self,id,**values):
        allowed={'status','done','total','message','worker_pid'}
        if not values or not set(values)<=allowed:raise ValueError('Некорректный прогресс')
        condition='' if values.get('status')=='cancelled' else ' AND status!="cancelled"'
        with self.db() as db:db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?'+condition,(*values.values(),id))
    def enqueue(self,bundle,path,name,purpose):
        self.get(bundle);suffix=Path(name).suffix.lower()
        # Две разные беды — две разные причины: «слишком большой» без числа не
        # говорит, на сколько частей резать, а «неподдержанный формат» без
        # расширения не говорит, что именно мы читаем.
        size=Path(path).stat().st_size
        if suffix not in FORMATS:raise ValueError('Формат файла "'+suffix+'" не поддерживается: '
            'нужен PDF, TXT, MD, JSON, ZIP или картинка (PNG, JPG, WEBP, SVG).')
        if size>MAX_FILE:raise ValueError('Файл весит '+str(round(size/1024/1024))+' МиБ, а предел — 384 МиБ. '
            'Разделите его на части; оригинал на вашем диске не изменён.')
        if purpose not in {'book','maps','handouts','tokens','patch','other'}:raise ValueError('Укажите назначение источника')
        if not name or len(name)>500 or '\x00' in name:raise ValueError('Некорректное имя')
        sha=self.put_file(path)
        with self.db() as db:
            old=db.execute('SELECT id FROM jobs WHERE bundle=? AND document=? AND purpose=? AND status NOT IN ("failed","cancelled") ORDER BY created DESC LIMIT 1',(bundle,sha,purpose)).fetchone()
            if old:return self.job(old[0])
            db.execute('INSERT OR IGNORE INTO documents(id,format,bytes) VALUES(?,?,?)',(sha,suffix,Path(path).stat().st_size))
            db.execute('INSERT OR IGNORE INTO bundle_files VALUES(?,?,?,?,?)',(bundle,sha,name,purpose,''))
            id=str(uuid.uuid4());db.execute('INSERT INTO jobs(id,bundle,document,original_name,purpose,status,created) VALUES(?,?,?,?,?,"queued",?)',(id,bundle,sha,name,purpose,time.time()))
        return self.job(id)
    def review(self,bundle):
        self.get(bundle)
        with self.db() as db:
            documents=[dict(r) for r in db.execute('SELECT distinct d.*,f.original_name,f.purpose FROM documents d JOIN bundle_files f ON f.document=d.id WHERE f.bundle=?',(bundle,))]
            candidates=[dict(r) for r in db.execute('SELECT c.* FROM candidates c WHERE c.document IN (SELECT document FROM bundle_files WHERE bundle=?) ORDER BY c.page LIMIT 1000',(bundle,))]
            media=[dict(r) for r in db.execute('SELECT m.* FROM materials m WHERE m.document IN (SELECT document FROM bundle_files WHERE bundle=?) ORDER BY m.document,m.page,m.region',(bundle,))]
            definition=db.execute('SELECT * FROM definitions WHERE bundle=?',(bundle,)).fetchone()
            modules=[dict(id=r['id'],body=json.loads(r['body']),revision=r['revision']) for r in db.execute('SELECT * FROM adventure_modules WHERE bundle=? ORDER BY id',(bundle,))]
        for row in documents:row['quality']=json.loads(row['quality'])
        for material in media:material['source_name']=next((d['original_name'] for d in documents if d['id']==material['document']),'')
        return dict(bundle=self.get(bundle),documents=documents,candidates=candidates,materials=media,
                    definition=json.loads(definition['body']) if definition else None,modules=modules,
                    readiness=self.readiness(bundle))
    def media_index(self,bundle):
        """What the importer offers: kinds, pages and boxes, without closed text.

        A candidate is listed so a person can confirm it in the review screen; the
        picture itself stays behind that screen, and the extracted text never
        leaves it. The same rows fill the player screen once a material is
        confirmed and revealed.
        """
        self.get(bundle)
        with self.db() as db:
            rows=[dict(r) for r in db.execute('SELECT m.id,m.document,m.page,m.kind,m.scope,m.status,m.region,m.bounds,m.caption,m.reviewed,m.sanitized FROM materials m WHERE m.document IN (SELECT document FROM bundle_files WHERE bundle=?) ORDER BY m.document,m.page,m.region',(bundle,))]
        for row in rows:row['bounds']=json.loads(row['bounds']) if row.get('bounds') else None
        counts={}
        for row in rows:counts[row['kind']]=counts.get(row['kind'],0)+1
        return dict(bundle=bundle,total=len(rows),kinds=counts,candidates=sum(1 for row in rows if row['status']=='candidate'),materials=rows)

    def public_media(self,bundle):
        """What a player may be shown, read without the closed text of the book.

        A printed name, a caption and the page are enough for the table to name a
        material out loud; the extracted page text stays behind the review screen,
        and only a text a person marked open (`public_text`) travels with it.
        """
        self.get(bundle)
        with self.db() as db:
            rows=[dict(r) for r in db.execute('SELECT m.id,m.document,m.page,m.kind,m.scope,m.status,m.region,m.caption,m.reviewed,m.sanitized,m.public_name,m.public_text,m.markup,(m.geometry IS NOT NULL) AS geometry FROM materials m WHERE m.document IN (SELECT document FROM bundle_files WHERE bundle=?) ORDER BY m.document,m.page,m.region',(bundle,))]
        return rows
    def material(self,id):
        with self.db() as db:row=db.execute('SELECT * FROM materials WHERE id=?',(id,)).fetchone()
        if not row:raise KeyError('Материал не найден')
        return dict(row)
    def material_text(self,id):
        # A review screen asks what the material itself says. A picture cut out of
        # a page is printed in its own box, so the lines of the box are the text of
        # the material and the lines around it belong to its neighbours; a page
        # material has no box and keeps its whole page.
        material=self.material(id)
        with self.db() as db:row=db.execute('SELECT p.content FROM pages p WHERE p.document=? AND p.number=?',(material['document'],material['page'])).fetchone()
        text=row['content'] if row else ''
        warning='Извлечённый текст может содержать невидимые элементы или секреты. Не публикуется автоматически.'
        found=dict(text=text,mode='page',page=material['page'],region=material.get('region') or '',warning=warning,lines=0,dropped=0)
        if not material.get('bounds'):return found
        from .module_media import text_in_box
        from .pdf_pipeline import load
        # Reading the layout of one page must not create tables: this is a GET.
        try:
            layout=load(self,material['document'],material['page'])
            box=text_in_box(layout,text,material['bounds'])
        except (ValueError,sqlite3.Error):
            found['warning']=warning+' Текст коробки собрать не удалось: раскладки страницы нет или границы материала не читаются. Отдан текст страницы целиком.'
            return found
        found.update(text=box['text'],mode='box',lines=box['lines'],dropped=box['dropped'])
        if not box['text']:
            if box['dropped']:found['warning']=warning+' В коробке материала нет напечатанного текста: это рисунок, и текст страницы к нему не относится.'
            else:found['warning']=warning+' В коробке материала не нашлось строк: у страницы нет текстового слоя или напечатанные строки остались без геометрии.'
        return found
    def set_material(self,id,*,scope,name,confirmed,sanitized,geometry=None,variant='',public_text=''):
        self.material(id)
        if scope not in {'gm','player','character'} or not confirmed:raise ValueError('Подтвердите проверку материала')
        if scope!='gm' and (not sanitized or not name.strip()):raise ValueError('Открытая версия требует проверки отсутствия секретов и открытого названия')
        if not 0<=len(name)<=120:raise ValueError('Слишком длинное название')
        if not isinstance(public_text,str) or len(public_text)>8000:raise ValueError('Текст открытой раздатки превышает 8000 символов')
        if geometry is not None:
            from .adventure_geometry import PreparedMap
            geometry=PreparedMap.model_validate(geometry).checked().model_dump()
        if variant and not self.material(variant):raise ValueError('Вариант не найден')
        with self.db() as db:db.execute('UPDATE materials SET scope=?,public_name=?,reviewed=1,sanitized=?,geometry=?,variant=?,public_text=?,status=\'confirmed\' WHERE id=?',(scope,name,int(sanitized),json.dumps(geometry) if geometry else None,variant,public_text,id))
        return self.material(id)
    def set_material_markup(self,id,value,state='ready',note=''):
        """Store what the picture shows. A failed read keeps the reason, not a markup."""
        self.material(id)
        if state not in ('ready','failed'):raise ValueError('Неизвестное состояние разметки')
        raw=json.dumps(value,ensure_ascii=False) if state=='ready' and value is not None else ''
        with self.db() as db:db.execute('UPDATE materials SET markup=?,markup_state=?,markup_note=? WHERE id=?',(raw,state,str(note or '')[:300],id))
        return self.material(id)
    def markup_pending(self,bundle,limit=5):
        """Materials nobody read yet, in book order; a failed read waits for a retry."""
        self.get(bundle);limit=max(1,min(int(limit),20))
        with self.db() as db:
            rows=db.execute('SELECT m.id,m.page,m.kind,m.caption FROM materials m WHERE m.document IN (SELECT document FROM bundle_files WHERE bundle=?) AND m.kind!=\'page\' AND m.markup=\'\' AND m.markup_state=\'\' ORDER BY m.document,m.page,m.region LIMIT ?',(bundle,limit)).fetchall()
        return [dict(row) for row in rows]
    def markup_status(self,bundle):
        """How much of the module is described in words: marked, failed, still silent.

        A material is *silent* when the book prints only a service name for it
        («Карта · стр. 87»): the kind and the page describe nothing a player could
        say at the table, so such a material answers only by the words the markup
        brings.
        """
        from .module_media import SERVICE_NAMES
        self.get(bundle)
        with self.db() as db:
            rows=db.execute('SELECT markup,markup_state,public_name,caption FROM materials WHERE document IN (SELECT document FROM bundle_files WHERE bundle=?) AND kind!=\'page\'',(bundle,)).fetchall()
        service={name for name in SERVICE_NAMES.values()}
        def described(row):
            for text in (row['public_name'],row['caption']):
                text=str(text or '').strip()
                if text and text not in service and '· стр.' not in text:return True
            return False
        total=len(rows);marked=sum(1 for row in rows if row['markup'])
        failed=sum(1 for row in rows if row['markup_state']=='failed')
        silent=sum(1 for row in rows if not row['markup'] and not described(row))
        return dict(bundle=bundle,total=total,marked=marked,failed=failed,left=total-marked-failed,silent=silent)
    def save_definition(self,bundle,body,confirmed):
        from .adventure_contracts import AdventureDefinition
        definition=AdventureDefinition.model_validate(body)
        if not confirmed:raise ValueError('Подтвердите проверку инструкций по первоисточнику')
        metadata=self.get(bundle)['metadata'];edition=str(metadata.get('edition','')).lower().replace('dnd','')
        # The book's edition and the mechanics edition are two different facts. A 2014
        # adventure is meant to run on the 2024 preset, so a difference is recorded as
        # the source edition and reported, never used to refuse the scenario: the
        # mapping layer states every replacement instead of blocking on the mismatch.
        if edition in ('2014','2024'):
            if not definition.source_ruleset:
                definition=definition.model_copy(update=dict(source_ruleset='dnd'+edition,source_ruleset_version=definition.ruleset_version))
            elif definition.source_ruleset in ('dnd2014','dnd2024') and definition.source_ruleset!='dnd'+edition:
                raise ValueError('Книга помечена редакцией '+edition+', а источник сценария указан как '+definition.source_ruleset+'. Подтвердите, какая редакция у текста.')
        if definition.source_ruleset and definition.source_ruleset not in ('dnd2014','dnd2024'):
            raise ValueError('Неизвестная редакция источника сценария')
        with self.db() as db:
            documents={r[0] for r in db.execute('SELECT document FROM bundle_files WHERE bundle=?',(bundle,))}
            for object in [*definition.events,*definition.tables,*definition.trackers,*definition.survival,*definition.companions,*definition.wilderness]:
                if object.source.document not in documents:raise ValueError('Происхождение не принадлежит комплекту')
                if object.source.review!='confirmed':raise ValueError('Инструкции требуют проверки')
                if object.source.page and not db.execute('SELECT 1 FROM pages WHERE document=? AND number=?',(object.source.document,object.source.page)).fetchone():raise ValueError('Страница источника не найдена')
            for id in definition.media:
                material=self.material(id)
                if material['document'] not in documents:raise ValueError('Материал другого комплекта')
            for span in definition.source_ranges:
                row=db.execute('SELECT pages FROM documents WHERE id=? AND status="extracted"',(span.document,)).fetchone()
                if span.document not in documents or row is None or span.last_page>row[0]:raise ValueError('Граница модуля вне извлечённых страниц')
            definition.reviewed=True
            db.execute('INSERT INTO definitions VALUES(?,?,1) ON CONFLICT(bundle) DO UPDATE SET body=excluded.body,revision=definitions.revision+1',(bundle,definition.model_dump_json(by_alias=True)))
            db.execute('INSERT INTO adventure_modules VALUES(?,?,?,1) ON CONFLICT(bundle,id) DO UPDATE SET body=excluded.body,revision=adventure_modules.revision+1',(bundle,definition.id,definition.model_dump_json(by_alias=True)))
            db.execute('UPDATE bundles SET revision=revision+1 WHERE id=?',(bundle,))
        return definition
    def definition(self,bundle,module=None):
        from .adventure_contracts import AdventureDefinition
        with self.db() as db:
            row=db.execute('SELECT body FROM adventure_modules WHERE bundle=? AND id=?',(bundle,module)).fetchone() if module else db.execute('SELECT body FROM definitions WHERE bundle=?',(bundle,)).fetchone()
        if not row:raise ValueError('Сценарные инструкции ещё не проверены')
        return AdventureDefinition.model_validate_json(row[0])
    def modules(self,bundle):
        """Every page range the book was cut into, with what it prints about it.

        An appendix is listed too, but as `kind='appendix'`: its places are real
        locations of the book, while it is not a scenario and never a start. The
        requirements (`minimum_level`, `recommended_party`, the continuation
        condition) come from the pages themselves and keep the page they are
        printed on, so a start screen can name where a number came from.
        """
        from .book_scene import requirements
        self.get(bundle)
        with self.db() as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM adventure_modules WHERE bundle=? ORDER BY id',(bundle,))]
            scenes={row['module']:json.loads(row['body']) for row in db.execute('SELECT module,body FROM module_scenes WHERE bundle=?',(bundle,))}
        result=[]
        for r in rows:
            scene=scenes.get(r['id']) or {}
            flags=r.get('alternate_flags') or {}
            section=scene.get('section') if isinstance(scene.get('section'),dict) else {}
            # Тип раздела живёт в разборе страниц, а не только в теле инструкций:
            # тело мастер правит, и перезапись не должна оживлять старт справочника.
            kind=section.get('kind') if section.get('kind') in ('appendix','adventure') else (flags.get('kind') or 'adventure')
            entry=scene.get('entry') if isinstance(scene.get('entry'),dict) else {}
            reference=flags.get('reference') or section.get('reference') or ''
            result.append(dict(id=r['id'],title=r['title'],kind=kind,ready=bool(r['reviewed'] and not r['unsupported']),
                startable=kind!='appendix',reference=reference,entry_scene=entry,
                reference_entries=len(scene.get('reference') or []),edits=list(scene.get('edits') or []),
                ruleset=r['ruleset'],ruleset_version=r['ruleset_version'],minimum_level=r.get('minimum_level'),
                maximum_level=r.get('maximum_level'),recommended_party=r.get('recommended_party'),
                source_pages=[[s.get('first_page'),s.get('last_page')] for s in r.get('source_ranges') or []],
                notes=list(r.get('unsupported') or []),requirements=requirements(scene,r['id']),scene=scene,
                locations=len(scene.get('locations') or []),npcs=len(scene.get('npcs') or []),
                checks=len(scene.get('checks') or [])))
        return result

    def entity_edits(self,bundle):
        """What a person corrected in this book, by document, page and printed name."""
        self.get(bundle)
        with self.db() as db:
            rows=db.execute('SELECT document,page,entity,name_key,name,value,created FROM scene_edits '
                            'WHERE bundle=? ORDER BY created',(bundle,)).fetchall()
        return [dict(row) for row in rows]

    def edit_entity(self,bundle,module,document,page,entity,name,value):
        """Store a person's correction of one entity type and apply it again.

        The correction lives apart from the parsed scene: the page, the printed
        name and the new type. A repeated parse re-reads the book, applies every
        stored correction, and nobody fixes the same line twice.
        """
        from . import scene_edits
        if entity not in scene_edits.ENTITIES:raise ValueError('Правка называет неизвестный вид строки: '+str(entity))
        if value not in scene_edits.VALUES or value not in scene_edits.ALLOWED.get(entity,()):
            raise ValueError('Правка называет недопустимый тип сущности: '+str(entity)+' → '+str(value))
        page=int(page or 0)
        if page<1:raise ValueError('Правка должна называть страницу книги')
        self.get(bundle)
        with self.db() as db:
            row=db.execute('SELECT body FROM module_scenes WHERE bundle=? AND module=?',(bundle,module)).fetchone()
        if row is None:raise ValueError('Модуль книги не разобран: правка не к чему')
        scene=json.loads(row['body'])
        if scene_edits.find(scene,entity,name,page) is None:
            raise ValueError('Такой сущности нет в сцене модуля: «'+str(name)+'», стр. '+str(page))
        # Источник правки — сцена модуля, а не поле запроса: строка, записанная под
        # чужим документом, не применилась бы уже никогда.
        document=str(scene.get('document') or document)
        # Сначала что получится, потом запись: правка, из которой не собирается
        # сцена, отклоняется до базы.
        scene_edits.apply(scene,[dict(entity=entity,name=name,page=page,value=value,document=document)])
        with self.db() as db:
            previous=db.execute('SELECT value,created FROM scene_edits WHERE bundle=? AND document=? AND page=? '
                                'AND entity=? AND name_key=?',(bundle,document,page,entity,scene_edits.key(name))).fetchone()
        try:
            with self.db() as db:
                db.execute('INSERT INTO scene_edits VALUES(?,?,?,?,?,?,?,?) '
                           'ON CONFLICT(bundle,document,page,entity,name_key) DO UPDATE SET value=excluded.value,'
                           'name=excluded.name,created=excluded.created',
                           (bundle,document,page,entity,scene_edits.key(name),name,value,time.time()))
            self.rebuild(bundle)
        except BaseException:
            # Сборка не прошла: правка возвращается к прежнему значению (или
            # снимается, если её не было) — работа человека не теряется, книга
            # остаётся разбираемой, а причину отказа он видит.
            with self.db() as db:
                if previous:
                    db.execute('UPDATE scene_edits SET value=?,created=? WHERE bundle=? AND document=? AND page=? '
                               'AND entity=? AND name_key=?',
                               (previous['value'],previous['created'],bundle,document,page,entity,scene_edits.key(name)))
                else:
                    db.execute('DELETE FROM scene_edits WHERE bundle=? AND document=? AND page=? AND entity=? AND name_key=?',
                               (bundle,document,page,entity,scene_edits.key(name)))
            raise
        return next((row for row in self.modules(bundle) if row['id']==module),{})

    def rebuild(self,bundle):
        """Re-apply every stored correction to the scenes and the worlds built from them.

        Correcting a monster read as a place must also take it out of the world of
        every episode that borrowed the appendix's places, so the worlds are built
        again from the corrected scenes instead of patched in one of them.
        """
        from . import scene_edits
        from .adventure_contracts import AdventureDefinition
        from .book_scene import entry_scene,scene_world,with_reference
        edits=self.entity_edits(bundle)
        texts={}
        def page_texts(document):
            if document not in texts:texts[document]=self.page_texts(document)
            return texts[document]
        with self.db() as db:
            modules=[(r['id'],r['body']) for r in db.execute('SELECT id,body FROM adventure_modules WHERE bundle=? ORDER BY id',(bundle,))]
            scenes={r['module']:r['body'] for r in db.execute('SELECT module,body FROM module_scenes WHERE bundle=?',(bundle,))}
        built={};definitions={}
        for identifier,body in modules:
            raw=scenes.get(identifier)
            if raw is None:continue
            scene=json.loads(raw);window=scene.get('pages') or [0,0]
            definition=json.loads(body)
            applied=[edit for edit in edits
                if edit['document']==scene.get('document') and int(window[0])<=int(edit['page'] or 0)<=int(window[1])]
            scene=scene_edits.apply(scene,applied)
            if applied:
                # Правка могла снять само место входа: оно пересчитывается по тем же
                # правилам, что и при разборе, а не остаётся указателем в пустоту.
                document=str(scene.get('document') or '')
                left,right=int(window[0]),int(window[1])
                scene['entry']=entry_scene(str(definition.get('title') or ''),scene.get('locations') or [],
                                           {number:text for number,text in page_texts(document).items()
                                            if left<=number<=right},document)
            built[identifier]=scene;definitions[identifier]=definition
        appendix=[scene_world(definitions[id]['title'],built[id])[0] for id in built
                  if (definitions[id].get('alternate_flags') or {}).get('kind')=='appendix']
        appendix=[world for world in appendix if world is not None]
        for identifier,scene in built.items():
            definition=definitions[identifier]
            world=scene_world(definition['title'],scene)[0]
            if world is not None and (definition.get('alternate_flags') or {}).get('kind')!='appendix':
                world=with_reference(world,appendix)
            definition['world']=world.model_dump(by_alias=True) if world is not None else None
            AdventureDefinition.model_validate(definition)
        with self.db() as db:
            for identifier,definition in definitions.items():
                db.execute('UPDATE adventure_modules SET body=?,revision=revision+1 WHERE bundle=? AND id=?',
                           (json.dumps(definition,ensure_ascii=False),bundle,identifier))
                db.execute('UPDATE module_scenes SET body=?,revision=revision+1 WHERE bundle=? AND module=?',
                           (json.dumps(built[identifier],ensure_ascii=False),bundle,identifier))
            # Проверенный сценарий комплекта читается и без имени модуля: его
            # собранный мир обязан быть тем же, что у исправленного модуля.
            aggregate=db.execute('SELECT body FROM definitions WHERE bundle=?',(bundle,)).fetchone()
            if aggregate:
                value=json.loads(aggregate['body'])
                if value.get('id') in definitions:
                    db.execute('UPDATE definitions SET body=?,revision=revision+1 WHERE bundle=?',
                               (json.dumps(definitions[value['id']],ensure_ascii=False),bundle))
            db.execute('UPDATE bundles SET revision=revision+1 WHERE id=?',(bundle,))
        return self.modules(bundle)
    def page_text(self,bundle,document,number):
        """One page exactly as the extractor read it; '' for an image page.

        A module cut from a book is a page range, so the scene it names is only
        as real as this text. Reading it back keeps the launch from inventing an
        opening the book never printed.
        """
        if not document or not number:return ''
        with self.db() as db:
            row=db.execute('SELECT p.content FROM pages p WHERE p.document=? AND p.number=? AND EXISTS(SELECT 1 FROM bundle_files f WHERE f.bundle=? AND f.document=p.document)',(document,number,bundle)).fetchone()
        return row[0] if row else ''

    def split_collection(self,bundle,document,entries,confirm):
        from .adventure_contracts import AdventureDefinition,SourceRange
        if not confirm:raise ValueError('Подтвердите границы отдельных модулей, а не порядок их прохождения')
        with self.db() as db:
            file=db.execute('SELECT d.pages FROM documents d JOIN bundle_files f ON f.document=d.id WHERE f.bundle=? AND d.id=? AND d.status="extracted"',(bundle,document)).fetchone()
        if not file or not 1<=len(entries)<=100:raise ValueError('Нет извлечённого сборника')
        spans=[];ids=set();created=[]
        for entry in entries:
            span=SourceRange(document=document,first_page=entry['first_page'],last_page=entry['last_page'])
            if span.last_page>file[0] or entry['id'] in ids or any(not (span.last_page<a or span.first_page>b) for a,b in spans):raise ValueError('Модули пересекаются, повторяются или выходят за страницы')
            ids.add(entry['id']);spans.append((span.first_page,span.last_page))
            definition=AdventureDefinition(id=entry['id'],title=entry['title'],ruleset=entry.get('ruleset','dnd2014'),ruleset_version=entry.get('ruleset_version',1),source_ranges=[span],continuation_of=entry.get('continuation_of',[]),unsupported=['Игровые инструкции модуля ещё не проверены'])
            created.append(definition)
        with self.db() as db:
            for definition in created:db.execute('INSERT OR IGNORE INTO adventure_modules VALUES(?,?,?,1)',(bundle,definition.id,definition.model_dump_json()))
        return self.modules(bundle)
    def chapter_rows(self,document):
        with self.db() as db:return [(r['page'],r['kind'],r['title']) for r in db.execute('SELECT page,kind,title FROM candidates WHERE document=? AND kind IN ("chapter","adventure") ORDER BY page,title',(document,))]

    def page_texts(self,document):
        """What each extracted page of a document prints, by page number."""
        with self.db() as db:return {row['number']:row['content'] for row in db.execute('SELECT number,content FROM pages WHERE document=?',(document,))}

    def chapters(self,document):
        """How many chapter boundaries the importer found, and on which pages.

        The page text goes with the headings: a heading printed once is only a
        boundary when its page carries content, so a contents page that names a
        chapter does not open it.
        """
        return chapter_starts(self.chapter_rows(document),self.page_texts(document))

    def auto_split(self,bundle,document,confirm=True,ruleset='',ruleset_version=1):
        """Propose page-range modules from the headings the importer found.

        A module built here is a page range, the title the book printed on that
        range, and the scene those pages state: the places the chapter names, the
        people it introduces and the checks it prints, each with its page and
        quote. Nothing is asked of a model — a book prints its own structure, and
        reading it is deterministic — and nothing is invented: an entity whose
        quote or name does not occur on its page is reported in `rejected` and
        kept out of the world.

        An appendix is not an episode. «Приложение А | Магические предметы» prints
        items, monsters and maps: offered as an adventure, it started a player
        inside a bestiary, and its page range counted as one more «модуль» beside
        the episodes. Its pages stay readable, the places it names travel to the
        world as locations, and it is stored with `alternate_flags.kind='appendix'`
        so no start ever picks it. Its boundary is still the book's own heading.

        Its boundaries were checked against the pages that were just read, which
        is what `reviewed` confirms here. When no heading can serve as a boundary,
        the whole document becomes one module and says so in `unsupported`, so it
        is not offered as ready.
        """
        from .adventure_contracts import AdventureDefinition,SourceRange
        from .book_scene import entry_scene,module_scene,scene_world
        if not confirm:raise ValueError('Подтвердите границы модулей, а не порядок их прохождения')
        with self.db() as db:
            row=db.execute('SELECT d.pages,d.status FROM documents d JOIN bundle_files f ON f.document=d.id WHERE f.bundle=? AND d.id=?',(bundle,document)).fetchone()
        if not row or row['status']!='extracted' or not 1<=row['pages']<=2000:raise ValueError('Нет извлечённого сборника для нарезки')
        pages=row['pages'];starts=self.chapters(document);spans=module_spans(starts,pages)
        # A reference volume (a bestiary, an item catalogue) has no episode at all.
        # Its sections stay appendices: the old fallback of one whole-document
        # «module» gave the catalogue a start button under another name.
        episodes=[span for span in spans if not is_reference(span[2])]
        reference=[span for span in spans if is_reference(span[2])]
        # Книга, в которой нет ни одного эпизода, — бестиарий или каталог: её
        # разделы остаются приложениями. Подмена «приложения → эпизод» давала
        # справочнику кнопку старта, то есть тот же дефект другим именем.
        if spans and not episodes:reference=list(spans)
        # What a book prints in front of its first chapter — a cover, credits, a
        # table of contents — is that chapter's material and not an episode of its
        # own: The Rise of Tiamat used to open on fourteen pages of title and
        # contents, so the first thing a player could start was not the game. Its
        # pages come with the first module as a second range (the maps and
        # handouts printed there stay readable), while the module's own range still
        # begins where the chapter begins: a campaign opens on the chapter's first
        # page, not on the cover.
        front=episodes[0][0] if episodes and episodes[0][0]>1 else 0
        unsupported=[]
        if not spans:
            from .book_sections import reference_kind
            # Документ без заголовков, но со статблоками или ценами на первой
            # странице — справочник, а не «эпизод на весь документ»: старт с него
            # не начинается, и раздел назван приложением.
            if reference_kind('',self.page_text(bundle,document,1) or '')=='reference':
                episodes=[(1,pages,'Весь документ: '+str(pages)+' стр.')]
                unsupported=['Границы глав не найдены: один модуль на весь документ']
            else:
                reference=[(1,pages,'Приложение: весь документ, '+str(pages)+' стр.')]
            starts=[]
        rule=str(ruleset or 'dnd2024');version=max(1,int(ruleset_version or 1));prefix='auto-'+document[:8]+'-'
        scenes={}
        edits=self.entity_edits(bundle)
        # Страницы книги читаются один раз: и справочник, и пересчёт входа после
        # правки смотрят в один и тот же текст.
        texts=None
        def page_texts():
            nonlocal texts
            if texts is None:texts=self.page_texts(document)
            return texts
        def build(rows,kind):
            """Page ranges of one kind: an episode, or an appendix of the book."""
            nonlocal texts
            built=[]
            for index,(first,last,title) in enumerate(rows,1):
                identifier=prefix+(('apx-' if kind=='appendix' else '')+str(index).zfill(2))
                name=(title.strip() or ('Приложение '+str(index) if kind=='appendix' else 'Модуль '+str(index)))
                notes=(['Приложение книги: справочные страницы, а не сценарий'] if kind=='appendix' else list(unsupported))
                # A document whose chapters could not be told apart is one page range
                # over the whole file, not a scene: the module already says in
                # `unsupported` that no boundary was found, so it is never offered as
                # ready, and scanning thousands of pages of a merged text for places
                # and people answers nothing a reader asked (a 9 MiB text took minutes
                # and left the import job unfinished). The scan runs only where a chapter
                # boundary was actually found.
                if unsupported and kind!='appendix':
                    scene=dict(document=document,pages=[first,last],locations=[],npcs=[],checks=[],rejected=[],
                               levels=dict(minimum=None,maximum=None,quote='',minimum_quote='',maximum_quote='',
                                           minimum_page=None,maximum_page=None),
                               party=None,continuation=None,counts=dict(locations=0,npcs=0,checks=0))
                    world=None
                else:
                    scene=module_scene(self,document,first,last,title=title,reference=kind=='appendix')
                    # Титульные страницы перед первой главой — материал первого
                    # эпизода: книга печатает там «для персонажей 8–15 уровня» и
                    # «идеальный размер группы — 4 персонажа». Места, люди и
                    # проверки по-прежнему берутся только из главы: из выходных
                    # данных введение не превращается в локацию.
                    if kind!='appendix' and index==1 and front:
                        from .book_scene import with_front_requirements
                        scene=with_front_requirements(self,document,1,front-1,scene)
                    # Типы разделов — раньше сущностей. Приложение книги это
                    # справочник (монстры, предметы, таблицы), а не эпизод: его
                    # страницы индексируются как справка, каждый вход со своей
                    # страницей и цитатой, и старт отсюда не начинается никогда.
                    if kind=='appendix':
                        from .book_sections import entries as reference_entries
                        from .book_sections import reference_kind
                        reference=reference_kind(name,self.page_text(bundle,document,first) or '')
                        scene['section']=dict(kind='appendix',reference=reference)
                        found,dropped=reference_entries({number:text for number,text in page_texts().items()
                                                         if first<=number<=last},reference,document)
                        scene['reference'],scene['reference_rejected']=found,dropped
                        for row in [*scene['locations'],*scene['npcs'],*scene['checks'],*scene['reference']]:
                            row['scope']='reference'
                    else:
                        scene['section']=dict(kind='adventure',reference='')
                    # Правка человека применяется и после повторного разбора: она
                    # хранится по странице и напечатанному имени, а не в сцене.
                    from . import scene_edits
                    applied=[edit for edit in edits
                        if edit['document']==document and first<=int(edit['page'] or 0)<=last]
                    scene=scene_edits.apply(scene,applied)
                    if applied:
                        # Правка могла снять место входа: оно пересчитывается, а не
                        # остаётся указателем на место, которого больше нет.
                        # Только страницы модуля: строка содержания книги называет
                        # эпизод, но вход в него — не оглавление.
                        scene['entry']=entry_scene(title,scene.get('locations') or [],
                            {number:text for number,text in page_texts().items()
                             if first<=number<=last},document)
                    # Приложение тоже получает мир: его места — настоящие места
                    # книги. Стартом оно не станет (kind='appendix'), но эпизод
                    # получает их ниже, чтобы в них можно было прийти.
                    world=scene_world(name,scene)[0]
                levels=scene.get('levels') if isinstance(scene.get('levels'),dict) else {}
                minimum=levels.get('minimum');maximum=levels.get('maximum')
                minimum=minimum if isinstance(minimum,int) and not isinstance(minimum,bool) else None
                maximum=maximum if isinstance(maximum,int) and not isinstance(maximum,bool) else None
                if minimum and maximum and minimum>maximum:minimum=maximum=None
                size=(scene.get('party') or {}).get('size')
                size=size if isinstance(size,int) and not isinstance(size,bool) and 1<=size<=12 else None
                scenes[identifier]=scene
                ranges=[SourceRange(document=document,first_page=first,last_page=last)]
                if kind!='appendix' and index==1 and front:ranges.append(SourceRange(document=document,first_page=1,last_page=front-1))
                built.append(AdventureDefinition(id=identifier,title=name[:150],ruleset=rule,ruleset_version=version,
                    source_ruleset=rule,source_ruleset_version=version,source_ranges=ranges,world=world,
                    minimum_level=minimum,maximum_level=maximum,recommended_party=size,reviewed=True,
                    unsupported=list(notes),alternate_flags=({'kind':'appendix','reference':scene['section'].get('reference') or 'reference'} if kind=='appendix' else {})))
            return built
        modules=build(episodes,'adventure');appendices=build(reference,'appendix');all_modules=modules+appendices
        # Места приложений уезжают в каждый эпизод как места книги: справочные
        # страницы не открывают кампанию, но город из приложения существует.
        from .book_scene import with_reference
        reference_worlds=[module.world for module in appendices if module.world is not None]
        for module in modules:module.world=with_reference(module.world,reference_worlds)
        with self.db() as db:
            db.execute('DELETE FROM adventure_modules WHERE bundle=? AND id LIKE ?',(bundle,prefix+'%'))
            db.execute('DELETE FROM module_scenes WHERE bundle=? AND module LIKE ?',(bundle,prefix+'%'))
            for definition in all_modules:
                db.execute('INSERT OR IGNORE INTO adventure_modules VALUES(?,?,?,1)',(bundle,definition.id,definition.model_dump_json()))
                db.execute('INSERT INTO module_scenes VALUES(?,?,?,1)',(bundle,definition.id,json.dumps(scenes[definition.id],ensure_ascii=False)))
            db.execute('UPDATE bundles SET revision=revision+1 WHERE id=?',(bundle,))
        def summary(module):return dict(id=module.id,title=module.title,first_page=module.source_ranges[0].first_page,
            last_page=module.source_ranges[0].last_page,minimum_level=module.minimum_level,
            recommended_party=module.recommended_party,locations=len(scenes[module.id]['locations']),
            npcs=len(scenes[module.id]['npcs']),checks=len(scenes[module.id]['checks']))
        return dict(document=document,pages=pages,chapters=len(starts),front=[1,front-1] if front else [],
                    modules=[summary(m) for m in modules],appendices=[summary(m) for m in appendices],
                    note=unsupported[0] if unsupported else '')

    def counts(self,bundle,document):
        # «12 модулей» на экране мастера — это 11 эпизодов и приложение,
        # посчитанные одной строкой. Импорт отвечает теми же двумя числами,
        # что и состояние книги: эпизоды можно начинать, приложения — нет.
        with self.db() as db:
            rows=db.execute("SELECT CASE WHEN json_extract(body,'$.alternate_flags.kind')='appendix' "
                            "THEN 'appendix' ELSE 'adventure' END AS kind,count(*) AS n "
                            "FROM adventure_modules WHERE bundle=? GROUP BY kind",(bundle,)).fetchall()
        counts={row['kind']:row['n'] for row in rows}
        return dict(chapters=len(self.chapters(document)),modules=sum(counts.values()),
                    episodes=counts.get('adventure',0),appendices=counts.get('appendix',0))

    def retrieve(self,bundle,query,limit=4):
        terms=re.findall(r'[\w-]{3,}',query)[:12]
        if not terms:return []
        match=' OR '.join('"'+word+'"' for word in terms)
        with self.db() as db:
            rows=db.execute('SELECT p.document,p.number,p.label,p.content FROM pages_fts JOIN pages p ON p.id=pages_fts.rowid WHERE pages_fts MATCH ? AND p.document IN (SELECT document FROM bundle_files WHERE bundle=?) ORDER BY rank LIMIT ?',(match,bundle,min(6,max(1,limit)))).fetchall()
        return [dict(document=r['document'],page=r['number'],printed_page=r['label'],text=r['content'][:1800],scope='gm') for r in rows]

class ImportJobs:
    def __init__(self,library):
        self.library=library;self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='adventure-import');self.children=set();self.closed=False;self.scheduled={};self.schedule_lock=threading.Lock();self.render_slots=threading.BoundedSemaphore(MAX_RENDERS)
    def submit(self,id):
        with self.schedule_lock:
            if id in self.scheduled:return self.scheduled[id]
            future=self.pool.submit(self.run,id);self.scheduled[id]=future
        def finished(done):
            with self.schedule_lock:
                if self.scheduled.get(id) is done:self.scheduled.pop(id,None)
        future.add_done_callback(finished)
        return future
    def resume(self):
        import psutil
        with self.library.db() as db:pending=[dict(r) for r in db.execute('SELECT id,status,worker_pid FROM jobs WHERE status IN ("queued","running")')]
        for job in pending:
            alive=False
            if job['worker_pid']:
                try:alive=job['id'] in psutil.Process(job['worker_pid']).cmdline()
                except (psutil.NoSuchProcess,psutil.AccessDenied):pass
            if not alive:
                self.library.progress(job['id'],status='queued',message='Импорт возобновляется после прерывания.');self.submit(job['id'])
    def run(self,id):
        if self.closed:return
        job=self.library.job(id)
        if job['status'] in ('complete','cancelled'):return
        args=[sys.executable,'--adventure-worker',str(self.library.data),id] if getattr(sys,'frozen',False) else [sys.executable,'-m','solo.adventure_worker',str(self.library.data),id]
        process=subprocess.Popen(args,cwd=Path(__file__).resolve().parents[1],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.children.add(process);started=time.monotonic()
        try:
            import psutil
            while process.poll() is None:
                try:rss=psutil.Process(process.pid).memory_info().rss
                except psutil.NoSuchProcess:break
                if self.closed or self.library.job(id)['status']=='cancelled' or time.monotonic()-started>360 or rss>768*1024*1024:
                    process.terminate();process.wait(timeout=5)
                    if self.closed:self.library.progress(id,status='queued',message='Импорт продолжится при следующем запуске.')
                    elif self.library.job(id)['status']!='cancelled':self.library.progress(id,status='failed',message='Лимит времени/памяти импорта. Оригинал сохранён.')
                    return
                time.sleep(.2)
            if self.closed and self.library.job(id)['status']!='complete':self.library.progress(id,status='queued',message='Импорт продолжится при следующем запуске.')
            elif process.returncode and self.library.job(id)['status'] not in ('cancelled','failed'):self.library.progress(id,status='failed',message='Импорт прерван. Оригинал сохранён; можно повторить.')
        finally:self.children.discard(process)
    def close(self):
        self.closed=True
        for process in list(self.children):
            if process.poll() is None:process.terminate()
        self.pool.shutdown(wait=False,cancel_futures=True)
    def render(self,id,width=1000):
        material=self.library.material(id)
        with self.library.db() as db:format=db.execute('SELECT format FROM documents WHERE id=?',(material['document'],)).fetchone()[0]
        width=max(160,min(int(width),4096));suffix='.svg' if format=='.svg' else '.png'
        from .module_media import render_name
        path=self.library.files/render_name(material['document'],material['page'],material.get('region'),width,suffix)
        if path.is_file():return path
        # A page with a huge picture is decoded inside the child, so the budget is
        # watched here: a preview that outgrows it is killed and reported as a
        # failed preview, while the backend keeps answering the player.
        if not self.render_slots.acquire(timeout=RENDER_WAIT):raise ValueError('Очередь превью занята; повторите через минуту')
        try:
            if path.is_file():return path
            args=[sys.executable,'--adventure-render',str(self.library.data),id,str(width)] if getattr(sys,'frozen',False) else [sys.executable,'-m','solo.adventure_worker','--render',str(self.library.data),id,str(width)]
            process=subprocess.Popen(args,cwd=Path(__file__).resolve().parents[1],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.children.add(process)
            try:
                import psutil
                started=time.monotonic()
                while process.poll() is None:
                    try:rss=psutil.Process(process.pid).memory_info().rss
                    except psutil.NoSuchProcess:break
                    if time.monotonic()-started>RENDER_SECONDS or rss>MAX_RENDER_RSS:
                        try:process.terminate();process.wait(timeout=5)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
                        raise ValueError('Превью превысило лимит времени/памяти; оригинал сохранён')
                    time.sleep(.05)
                if process.returncode or not path.is_file():raise ValueError('Страница не отрисована; оригинал сохранён')
            finally:self.children.discard(process)
        finally:self.render_slots.release()
        return path
