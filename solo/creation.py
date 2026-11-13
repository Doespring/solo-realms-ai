"""Player-facing creation drafts, backed by the existing preparation and library."""
import asyncio,threading,time
from .preparation import Draft
from .adventure_library import AUTO_SPLIT_KEY

class CreationService:
    def __init__(self,preparation,library,jobs):
        self.preparation=preparation;self.library=library;self.jobs=jobs;self.lock=threading.RLock()
        with preparation.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS creation_files(draft TEXT NOT NULL REFERENCES drafts(id),document TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(draft,document))')
    def owned(self,id):
        draft=self.preparation.get(id)
        if draft['body'].get('flow')!='simple':
            # The eight-step master and the launch are one state machine with one
            # draft: a finished master draft is migrated in place, and a half-done
            # one is refused with the list of parts it lacks instead of a second
            # empty draft being created for it (A03/F01).
            from .creation_handoff import record as handoff_record
            state=handoff_record(self.preparation,id,library=self.library)
            if not state['ready']:
                raise ValueError('Черновик ещё не готов к игре: '+'; '.join(row['text'] for row in state['missing'])+'. Откройте его в подготовке и дозаполните.')
            # adopt() answers with the launch view (files, status); owned() must
            # keep returning the saved preparation draft itself.
            self.adopt(id)
            draft=self.preparation.get(id)
        return draft
    def handoff(self,id):
        """What the launch must know about this draft, named field by field."""
        from .creation_handoff import record
        return record(self.preparation,id,library=self.library)
    def attach_bundle(self,id,bundle):
        """The book's own files become this draft's selected materials."""
        if not bundle:return
        with self.library.db() as db:
            roots=[row[0] for row in db.execute('SELECT document FROM bundle_files WHERE bundle=? AND parent=""',(bundle,))]
        with self.preparation.connection() as db:
            for document in roots:
                db.execute('INSERT INTO creation_files VALUES(?,?,1) ON CONFLICT(draft,document) DO UPDATE SET active=1',(id,document))
    def adopt(self,id,*,module_id=None,hero_draft=None,equipment=None,revision=None):
        """Migrate a finished master draft into the launch, in place.

        The screen hands over the four parts it owns (draft, module, hero, items)
        and they are checked against the saved draft: what the player approved in
        the master is exactly what the launch starts with. A draft that still
        lacks a part answers with the list instead of opening an empty screen,
        and no second draft is ever created.
        """
        from .creation_handoff import record
        with self.lock:
            draft=self.preparation.get(id)
            if revision is not None and int(revision)!=draft['revision']:
                raise ValueError('Черновик изменился: сохраните шаг мастера и повторите выбор героя.')
            state=record(self.preparation,id,library=self.library)
            if module_id is not None and str(module_id)!=state['module_id']:
                raise ValueError('Выбранное приключение изменилось: обновите шаг мастера.')
            if equipment is not None and list(equipment)!=state['equipment']:
                raise ValueError('Снаряжение изменилось: обновите шаг мастера.')
            if hero_draft is not None and str((hero_draft or {}).get('name') or '')!=str((state['hero_draft'] or {}).get('name') or ''):
                raise ValueError('Герой изменился: обновите шаг мастера.')
            if not state['ready']:
                # The screen lists the missing parts and offers the step that fills
                # each one; nothing is migrated and no draft is created.
                return dict(ready=False,draft=draft,handoff=state,missing=state['missing'])
            body=Draft.model_validate(draft['body'])
            if not draft['campaign'] and body.flow!='simple':
                body.flow='simple'
                self.preparation.save(id,body,draft['revision'])
            self.attach_bundle(id,body.adventure_bundle)
            state=record(self.preparation,id,library=self.library)
            return dict(ready=True,draft=self.get(id),handoff=state,missing=[])
    def new(self):
        with self.lock:
            draft=self.preparation.create();body=Draft.model_validate(draft['body']);body.flow='simple'
            # A player's draft is a whole book they mean to play, so the importer
            # may propose its page ranges by itself: without this flag every book
            # downloaded from a store imported as «Материалы извлечены» and zero
            # modules, and the whole adventure had to be cut by hand. A bundle
            # assembled in the library keeps the reviewed split path.
            body.adventure_bundle=self.library.create('Материалы нового приключения',{AUTO_SPLIT_KEY:True})['id']
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
            rebuilt=self.rebuild_modules(target)
            return dict(**self.get(id),modules=sum(len(item['modules']) for item in rebuilt),module_failures=[item['error'] for item in rebuilt if item.get('error')])
    def rebuild_modules(self,bundle):
        """Cut the bundle's books again, the way a fresh import cuts them.

        A draft that points at a library bundle used to inherit that bundle's
        modules as they stood, so a book re-cut by a newer importer still opened
        on the ranges of the old one — The Rise of Tiamat still began with its
        own contents page. The same rule that cuts a fresh import is applied here,
        nothing waits for someone to press «заново», and a bundle assembled by
        hand keeps its reviewed split (no `auto_split` in its metadata).
        """
        if not self.library.get(bundle)['metadata'].get(AUTO_SPLIT_KEY):return []
        with self.library.db() as db:
            books=[dict(row) for row in db.execute('SELECT f.document,f.purpose,d.format,d.status FROM bundle_files f JOIN documents d ON d.id=f.document WHERE f.bundle=? AND f.parent="" ORDER BY f.original_name',(bundle,))]
        rebuilt=[]
        for row in books:
            if row['purpose']!='book' or row['format'] not in ('.pdf','.txt','.md') or row['status']!='extracted':continue
            # A book the importer refuses (too many pages, no boundary at all) must
            # not take the rest of the draft down with it, so its reason travels
            # back in the answer instead of being dropped silently.
            try:rebuilt.append(self.library.auto_split(bundle,row['document']))
            except ValueError as error:rebuilt.append(dict(document=row['document'],modules=[],error=str(error)))
        return rebuilt
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
        # Reading one draft by id migrates a finished master draft in place.
        return self.view(self.owned(id))
    def view(self,draft):
        # A read-only projection: listing the player's library must not write.
        id=draft['id'];bundle=draft['body']['adventure_bundle']
        with self.preparation.connection() as db:active={r[0] for r in db.execute('SELECT document FROM creation_files WHERE draft=? AND active=1',(id,))}
        with self.library.db() as db:
            roots=[dict(r) for r in db.execute('SELECT d.id,d.format,d.bytes,f.original_name FROM documents d JOIN bundle_files f ON f.document=d.id WHERE f.bundle=? AND f.parent=""',(bundle,))]
            jobs=[dict(r) for r in db.execute('SELECT id,document,status,done,total,message FROM jobs WHERE bundle=? ORDER BY created DESC',(bundle,))]
        files=[]
        for root in roots:
            if root['id'] not in active:continue
            job=next((j for j in jobs if j['document']==root['id']),None)
            files.append(dict(document=root['id'],sha256=root['id'],name=root['original_name'],format=root['format'],bytes=root['bytes'],job=job))
        status='playing' if draft['campaign'] else 'importing' if any(f['job'] and f['job']['status'] in ('queued','running') for f in files) else 'ready' if draft['body']['prepared'] else 'draft'
        # The launch reads the master's draft through one record: which draft,
        # which module, which hero text and which items came from the player.
        from .creation_handoff import record
        return dict(id=id,revision=draft['revision'],updated=draft['updated'],name=draft['body']['name'],description=draft['body'].get('description',''),campaign=draft['campaign'],status=status,files=files,handoff=record(self.preparation,id,library=self.library))
    def list(self):
        # A03/F01: a finished draft of the eight-step master is listed in the
        # player's own library before its first launch too. Opening it migrates
        # the same draft into the launch contract; a half-written one stays out
        # (the launch names its missing parts instead of showing it as ready).
        # The projection is read-only: a listing must not bump a revision.
        from .creation_handoff import record
        rows=[]
        for saved in self.preparation.list():
            draft=self.preparation.get(saved['id'])
            if draft['body'].get('flow')=='simple' or (
                    not draft['campaign'] and record(self.preparation,saved['id'],library=self.library)['ready']):
                rows.append(self.view(draft))
        return rows

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
    class Adopt(Strict):
        # The four parts the master's last step hands over, named explicitly; the
        # draft id in the body must be the draft in the path.
        draft_id: str = Field(default='',max_length=80)
        revision: int | None = Field(default=None,ge=0)
        module_id: str | None = Field(default=None,max_length=80)
        hero_draft: dict | None = None
        equipment: list[str] | None = Field(default=None,max_length=20)
    @app.get('/api/creation/drafts/{id}')
    def get(id:str):return dict(**service.get(id),preparation=pipeline.latest(id))
    @app.get('/api/creation/drafts/{id}/handoff')
    def handoff(id:str):return service.handoff(id)
    @app.post('/api/creation/drafts/{id}/adopt')
    async def adopt(id:str,body:Adopt):
        """The master's last step hands its own draft to the launch (A03/F01).

        Returns ``ready: false`` with the missing parts instead of opening an
        empty adventure, and migrates the same draft when nothing is missing.
        """
        if body.draft_id and body.draft_id!=id:raise ValueError('Черновик изменился: откройте мастер заново.')
        payload=body.model_dump();payload.pop('draft_id')
        async with game.operation:return await asyncio.to_thread(service.adopt,id,**payload)
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
    @app.post('/api/creation/runs/{id}/retry')
    async def retry(id:str):return await pipeline.retry(id)
