"""Player-facing creation drafts, backed by the existing preparation and library."""
import asyncio,json,threading,time
from .preparation import Draft

class CreationService:
    def __init__(self,preparation,library,jobs):
        self.preparation=preparation;self.library=library;self.jobs=jobs;self.lock=threading.RLock()
        with preparation.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS creation_files(draft TEXT NOT NULL REFERENCES drafts(id),document TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(draft,document))')
    def owned(self,id):
        draft=self.preparation.get(id)
        if draft['body'].get('flow')!='simple':raise ValueError('Это черновик расширенного редактора. Откройте его через Advanced.')
        return draft
    def new(self):
        with self.lock:
            draft=self.preparation.create();body=Draft.model_validate(draft['body']);body.flow='simple'
            body.adventure_bundle=self.library.create('Материалы нового приключения')['id']
            self.preparation.save(draft['id'],body,draft['revision'])
            return self.get(draft['id'])
    def save(self,id,description,revision):
        with self.lock:
            draft=self.owned(id);body=Draft.model_validate(draft['body'])
            if body.description!=description:body.prepared=False
            body.description=description
            self.preparation.save(id,body,revision)
            return self.get(id)
    def from_library(self,bundle):
        source=self.library.get(bundle)
        if not source['import_complete']:raise ValueError('Материалы библиотеки ещё не извлечены')
        with self.lock:
            draft=self.new();id=draft['id'];target=self.owned(id)['body']['adventure_bundle']
            with self.library.db() as db:
                rows=db.execute('SELECT * FROM bundle_files WHERE bundle=?',(bundle,)).fetchall()
                for row in rows:db.execute('INSERT OR IGNORE INTO bundle_files VALUES(?,?,?,?,?)',(target,row['document'],row['original_name'],row['purpose'],row['parent']))
                for row in rows:
                    if not row['parent']:
                        import uuid
                        db.execute('INSERT INTO jobs(id,bundle,document,original_name,purpose,status,created) VALUES(?,?,?,?,?,"complete",?)',(str(uuid.uuid4()),target,row['document'],row['original_name'],row['purpose'],time.time()))
            for row in rows:
                if not row['parent']:self.select(id,row['document'])
            return self.get(id)
    def select(self,id,document,active=True):
        with self.lock:
            draft=self.owned(id)
            if draft['campaign']:raise ValueError('Приключение уже начато')
            if active:
                with self.preparation.connection() as db:
                    selected={r[0] for r in db.execute('SELECT document FROM creation_files WHERE draft=? AND active=1',(id,))}
                if document not in selected and len(selected)>=20:raise ValueError('Можно выбрать до 20 исходных файлов')
            with self.library.db() as db:
                if not db.execute('SELECT 1 FROM bundle_files WHERE bundle=? AND document=? AND parent=""',(draft['body']['adventure_bundle'],document)).fetchone():raise ValueError('Источник не принадлежит этому черновику')
            with self.preparation.connection() as db:
                db.execute('INSERT INTO creation_files VALUES(?,?,?) ON CONFLICT(draft,document) DO UPDATE SET active=excluded.active',(id,document,int(active)))
                body=Draft.model_validate(draft['body']);body.prepared=False
                db.execute('UPDATE drafts SET body=?,revision=revision+1,updated=? WHERE id=?',(body.model_dump_json(),time.time(),id))
            return self.get(id)
    def allowed_documents(self,id):
        draft=self.owned(id)
        with self.preparation.connection() as db:active={r[0] for r in db.execute('SELECT document FROM creation_files WHERE draft=? AND active=1',(id,))}
        with self.library.db() as db:edges=[tuple(r) for r in db.execute('SELECT document,parent FROM bundle_files WHERE bundle=?',(draft['body']['adventure_bundle'],))]
        allowed=set(active)
        for _ in range(4):allowed.update(child for child,parent in edges if parent in allowed)
        return allowed
    def get(self,id):
        draft=self.owned(id);bundle=draft['body']['adventure_bundle']
        with self.preparation.connection() as db:active={r[0] for r in db.execute('SELECT document FROM creation_files WHERE draft=? AND active=1',(id,))}
        with self.library.db() as db:
            roots=[dict(r) for r in db.execute('SELECT d.id,d.format,d.bytes,d.status,d.quality,f.original_name FROM documents d JOIN bundle_files f ON f.document=d.id WHERE f.bundle=? AND f.parent=""',(bundle,))]
            jobs=[dict(r) for r in db.execute('SELECT id,document,status,done,total,message FROM jobs WHERE bundle=? ORDER BY created DESC',(bundle,))]
        files=[]
        for root in roots:
            if root['id'] not in active:continue
            job=next((j for j in jobs if j['document']==root['id']),None)
            # Причина отказа едет вместе с файлом: без неё экран показывал
            # «Не удалось извлечь» и человек не знал, что PDF — скан без текста.
            try:quality=json.loads(root['quality'] or '{}')
            except ValueError:quality={}
            files.append(dict(document=root['id'],sha256=root['id'],name=root['original_name'],format=root['format'],bytes=root['bytes'],status=root['status'],quality=quality,job=job))
        status='playing' if draft['campaign'] else 'importing' if any(f['job'] and f['job']['status'] in ('queued','running') for f in files) else 'ready' if draft['body']['prepared'] else 'draft'
        return dict(id=id,revision=draft['revision'],updated=draft['updated'],name=draft['body']['name'],description=draft['body'].get('description',''),campaign=draft['campaign'],status=status,files=files)
    def list(self):
        return [self.get(d['id']) for d in self.preparation.list() if self.preparation.get(d['id'])['body'].get('flow')=='simple']

def register_creation_api(app,store,game):
    from fastapi import UploadFile,File,Request,HTTPException
    from pydantic import Field
    from .contracts import Strict
    from .source_upload import receive_source
    service=CreationService(app.state.preparation,app.state.adventures,app.state.adventure_jobs);app.state.creation=service
    from .preparation_pipeline import PreparationPipeline
    from .preparation_contracts import PrepareRequest
    pipeline=PreparationPipeline(service,game.llm,store);app.state.preparation_pipeline=pipeline
    class Save(Strict):
        description:str=Field(max_length=12000)
        revision:int=Field(ge=0)
    class ConfirmWorld(Strict):
        expected_revision:int=Field(ge=0)
        run_id:str=Field(min_length=1,max_length=80)
        confirm:bool=False
    class StartPlace(Strict):
        expected_revision:int=Field(ge=0)
        name:str=Field(min_length=1,max_length=120)
    @app.post('/api/creation/drafts')
    async def new():
        async with game.operation:return service.new()
    @app.get('/api/creation/drafts')
    def drafts():return service.list()
    @app.post('/api/creation/from-library/{bundle}')
    async def from_library(bundle:str):
        async with game.operation:return service.from_library(bundle)
    @app.get('/api/creation/drafts/{id}')
    def get(id:str):return dict(**service.get(id),preparation=pipeline.latest(id))
    @app.put('/api/creation/drafts/{id}')
    async def save(id:str,body:Save):
        async with game.operation:return service.save(id,body.description,body.revision)
    @app.post('/api/creation/drafts/{id}/files')
    async def upload(id:str,request:Request,file:UploadFile=File(...)):
        draft=service.owned(id)
        if draft['campaign']:raise ValueError('Приключение уже начато')
        if len(service.get(id)['files'])>=20:raise ValueError('Можно выбрать до 20 исходных файлов за одну подготовку')
        job=await receive_source(service.library,service.jobs,draft['body']['adventure_bundle'],file,request=request)
        async with game.operation:return service.select(id,job['document'])
    @app.delete('/api/creation/drafts/{id}/files/{document}')
    async def exclude(id:str,document:str):
        async with game.operation:return service.select(id,document,False)
    @app.post('/api/creation/drafts/{id}/files/{document}/retry')
    async def retry(id:str,document:str):
        draft=service.get(id);source=next((f for f in draft['files'] if f['document']==document),None)
        if source is None:raise KeyError('Источник не выбран')
        bundle=service.owned(id)['body']['adventure_bundle']
        job=service.library.enqueue(bundle,service.library.blob(document),source['name'],'book');service.jobs.submit(job['id'])
        return service.get(id)
    @app.post('/api/creation/drafts/{id}/prepare')
    async def prepare(id:str,body:PrepareRequest):return await pipeline.start(id,body)
    @app.post('/api/creation/drafts/{id}/readiness')
    async def readiness(id:str):
        async with game.operation:return pipeline.recheck_start(id)
    @app.get('/api/creation/drafts/{id}/world-readiness')
    def world_readiness(id:str):
        from .prototype_prepared import prepared_start_readiness
        service.owned(id)
        return prepared_start_readiness(service.preparation,id)
    @app.post('/api/creation/drafts/{id}/confirm-world')
    async def confirm_world(id:str,body:ConfirmWorld):
        from .prototype_prepared import prepared_start_readiness
        async with game.operation:
            service.owned(id)
            await asyncio.to_thread(pipeline.confirm_world,id,**body.model_dump())
            return dict(**service.get(id),world_readiness=prepared_start_readiness(service.preparation,id))
    @app.post('/api/creation/drafts/{id}/start-place')
    async def start_place(id:str,body:StartPlace):
        """The opening place agreed in dialogue, recorded without a new model call."""
        from .prototype_prepared import prepared_start_readiness
        async with game.operation:
            service.owned(id)
            await asyncio.to_thread(pipeline.settle_start,id,**body.model_dump())
            return dict(**service.get(id),world_readiness=prepared_start_readiness(service.preparation,id))
    @app.get('/api/creation/runs/{id}')
    def progress(id:str):return pipeline.status(id)
    @app.post('/api/creation/runs/{id}/cancel')
    async def cancel(id:str):return await pipeline.cancel(id)
