"""Private, content-addressed adventure library; originals never enter installed assets."""
from contextlib import contextmanager
import hashlib, json, os, re, sqlite3, subprocess, sys, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MAX_FILE=384*1024*1024
MAX_PACKAGE=768*1024*1024
MAX_FILES=300
FORMATS={'.pdf','.txt','.md','.json','.zip','.png','.jpg','.jpeg','.webp','.svg'}

def file_size(count):
    """Размер так, как его прочитает человек: байты, КиБ или МиБ."""
    if count<1024:return str(count)+' байт'
    if count<1048576:return ('%.1f'%(count/1024)).replace('.',',')+' КиБ'
    return ('%.1f'%(count/1048576)).replace('.',',')+' МиБ'

def stable(*values):
    return hashlib.sha256(json.dumps(values,ensure_ascii=False).encode()).hexdigest()[:32]

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

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
            CREATE TABLE IF NOT EXISTS materials(id TEXT PRIMARY KEY,document TEXT NOT NULL REFERENCES documents(id),page INTEGER NOT NULL,kind TEXT NOT NULL,scope TEXT NOT NULL DEFAULT 'gm',public_name TEXT NOT NULL DEFAULT '',reviewed INTEGER NOT NULL DEFAULT 0,sanitized INTEGER NOT NULL DEFAULT 0,geometry TEXT,variant TEXT NOT NULL DEFAULT '',UNIQUE(document,page));
            CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,document TEXT NOT NULL REFERENCES documents(id),page INTEGER NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,body TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'requires_review');
            CREATE TABLE IF NOT EXISTS definitions(bundle TEXT PRIMARY KEY REFERENCES bundles(id),body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS adventure_modules(bundle TEXT NOT NULL REFERENCES bundles(id),id TEXT NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(bundle,id));
            CREATE TABLE IF NOT EXISTS analysis_cache(key TEXT PRIMARY KEY,body TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,bundle TEXT NOT NULL REFERENCES bundles(id),document TEXT NOT NULL REFERENCES documents(id),original_name TEXT NOT NULL,purpose TEXT NOT NULL,status TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT '',created REAL NOT NULL);
            PRAGMA user_version=1;''')
            if 'worker_pid' not in {row[1] for row in db.execute('PRAGMA table_info(jobs)')}:db.execute('ALTER TABLE jobs ADD COLUMN worker_pid INTEGER')
            if 'public_text' not in {row[1] for row in db.execute('PRAGMA table_info(materials)')}:db.execute('ALTER TABLE materials ADD COLUMN public_text TEXT NOT NULL DEFAULT ""')
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
    def get(self,id):
        with self.db() as db:
            row=db.execute('SELECT * FROM bundles WHERE id=?',(id,)).fetchone()
            if row is None:raise KeyError('Комплект не найден')
            counts=db.execute('SELECT count(distinct d.id),sum(d.pages),sum(d.status="extracted") FROM documents d JOIN (SELECT distinct document FROM bundle_files WHERE bundle=?) f ON f.document=d.id',(id,)).fetchone()
            definition=db.execute('SELECT body FROM definitions WHERE bundle=?',(id,)).fetchone()
            jobs=[dict(r) for r in db.execute('SELECT id,status,done,total,message FROM jobs j WHERE bundle=? AND created=(SELECT max(created) FROM jobs old WHERE old.bundle=j.bundle AND old.document=j.document AND old.purpose=j.purpose) ORDER BY created DESC LIMIT 20',(id,))]
            status='documents_imported' if counts[0] and counts[2]==counts[0] else 'import_incomplete' if counts[0] else 'catalog_only'
            if definition:
                body=json.loads(definition[0]);status='instructions_reviewed' if counts[0] and counts[2]==counts[0] and body.get('reviewed') and not body.get('unsupported') else 'requires_review'
            configured=json.loads(definition[0]) if definition else {}
            return dict(id=id,title=row['title'],metadata=json.loads(row['metadata']),revision=row['revision'],documents=counts[0],pages=counts[1] or 0,status=status,jobs=jobs,ruleset=configured.get('ruleset'),ruleset_version=configured.get('ruleset_version'),import_complete=bool(counts[0] and counts[2]==counts[0]))
    def list(self):
        with self.db() as db:ids=[r[0] for r in db.execute('SELECT id FROM bundles ORDER BY created DESC')]
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
        self.get(bundle);suffix=Path(name).suffix.lower();size=Path(path).stat().st_size
        if suffix not in FORMATS:raise ValueError('Неподдержанный формат «'+(suffix or 'без расширения')+'»: нужен PDF, TXT, MD, JSON, ZIP или изображение.')
        if size>MAX_FILE:raise ValueError('Файл весит '+file_size(size)+' — предел '+file_size(MAX_FILE)+'. Оригинал на вашем диске не изменён.')
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
            media=[dict(r) for r in db.execute('SELECT m.* FROM materials m WHERE m.document IN (SELECT document FROM bundle_files WHERE bundle=?) ORDER BY m.document,m.page',(bundle,))]
            definition=db.execute('SELECT * FROM definitions WHERE bundle=?',(bundle,)).fetchone()
            modules=[dict(id=r['id'],body=json.loads(r['body']),revision=r['revision']) for r in db.execute('SELECT * FROM adventure_modules WHERE bundle=? ORDER BY id',(bundle,))]
        for row in documents:row['quality']=json.loads(row['quality'])
        for material in media:material['source_name']=next((d['original_name'] for d in documents if d['id']==material['document']),'')
        return dict(bundle=self.get(bundle),documents=documents,candidates=candidates,materials=media,definition=json.loads(definition['body']) if definition else None,modules=modules)
    def material(self,id):
        with self.db() as db:row=db.execute('SELECT * FROM materials WHERE id=?',(id,)).fetchone()
        if not row:raise KeyError('Материал не найден')
        return dict(row)
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
        with self.db() as db:db.execute('UPDATE materials SET scope=?,public_name=?,reviewed=1,sanitized=?,geometry=?,variant=?,public_text=? WHERE id=?',(scope,name,int(sanitized),json.dumps(geometry) if geometry else None,variant,public_text,id))
        return self.material(id)
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
        self.get(bundle)
        with self.db() as db:rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM adventure_modules WHERE bundle=? ORDER BY id',(bundle,))]
        return [dict(id=r['id'],title=r['title'],ready=bool(r['reviewed'] and not r['unsupported']),ruleset=r['ruleset'],ruleset_version=r['ruleset_version'],minimum_level=r.get('minimum_level'),maximum_level=r.get('maximum_level'),recommended_party=r.get('recommended_party')) for r in rows]
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
    def retrieve(self,bundle,query,limit=4):
        terms=re.findall(r'[\w-]{3,}',query)[:12]
        if not terms:return []
        match=' OR '.join('"'+word+'"' for word in terms)
        with self.db() as db:
            rows=db.execute('SELECT p.document,p.number,p.label,p.content FROM pages_fts JOIN pages p ON p.id=pages_fts.rowid WHERE pages_fts MATCH ? AND p.document IN (SELECT document FROM bundle_files WHERE bundle=?) ORDER BY rank LIMIT ?',(match,bundle,min(6,max(1,limit)))).fetchall()
        return [dict(document=r['document'],page=r['number'],printed_page=r['label'],text=r['content'][:1800],scope='gm') for r in rows]

class ImportJobs:
    def __init__(self,library):
        self.library=library;self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='adventure-import');self.children=set();self.closed=False;self.scheduled={};self.schedule_lock=threading.Lock()
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
        path=self.library.files/(material['document']+'-p'+str(material['page'])+'-w'+str(width)+suffix)
        if path.is_file():return path
        args=[sys.executable,'--adventure-render',str(self.library.data),id,str(width)] if getattr(sys,'frozen',False) else [sys.executable,'-m','solo.adventure_worker','--render',str(self.library.data),id,str(width)]
        process=subprocess.Popen(args,cwd=Path(__file__).resolve().parents[1],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.children.add(process)
        try:
            process.wait(timeout=35)
            if process.returncode or not path.is_file():raise ValueError('Страница не отрисована; оригинал сохранён')
        except subprocess.TimeoutExpired:
            process.terminate();process.wait(timeout=5);raise ValueError('Превью превысило лимит времени') from None
        finally:self.children.discard(process)
        return path
