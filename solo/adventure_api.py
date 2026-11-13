"""Separate preparation/review access from the player media projection."""
import asyncio,json,secrets,time,uuid
from pathlib import Path
from typing import Literal
from fastapi import Request,UploadFile,File,Form,HTTPException
from fastapi.responses import FileResponse,JSONResponse
from pydantic import Field
from .contracts import Strict
from .adventure_library import AdventureLibrary,ImportJobs,MAX_FILE

class BundleCreate(Strict):
    title: str=Field(min_length=1,max_length=150)
    metadata: dict=Field(default_factory=dict)
class ReviewConsent(Strict):
    confirm_spoilers: bool
class MaterialReview(Strict):
    scope: Literal['gm','player','character']='gm'
    name: str=Field(default='',max_length=120)
    confirmed: bool=False
    sanitized: bool=False
    geometry: dict | None=None
    variant: str=Field(default='',max_length=80)
    public_text: str=Field(default='',max_length=8000)
class DefinitionReview(Strict):
    definition: dict
    confirmed: bool=False
class AttachAdventure(Strict):
    bundle: str=Field(max_length=80)
    request_id: str=Field(min_length=1,max_length=80)
    confirm: bool=False
    module: str=Field(default='',max_length=80)
class ContinueAdventure(AttachAdventure):
    module: str=Field(min_length=1,max_length=80)
    standalone: bool=False
class MaterialMarkupBatch(Strict):
    limit: int = Field(default=5, ge=1, le=20)
    request_id: str = Field(default='', max_length=80)
class CollectionSplit(Strict):
    document: str=Field(max_length=80)
    entries: list[dict]=Field(min_length=1,max_length=100)
    confirmed: bool=False
class EntityEdit(Strict):
    #: A person corrects the type of one line the parser read: the module it
    #: stands in, the page it is printed on, its printed name and what it is.
    module: str=Field(min_length=1,max_length=120)
    document: str=Field(min_length=1,max_length=80)
    page: int=Field(ge=1,le=5000)
    entity: str=Field(min_length=1,max_length=20)
    name: str=Field(default='',max_length=200)
    value: str=Field(min_length=1,max_length=20)
class EncounterCompletion(Strict):
    encounter: str=Field(min_length=1,max_length=160)
    request_id: str=Field(min_length=1,max_length=80)
    confirmed: bool=False

def register_adventure_api(app,store,game):
    library=AdventureLibrary(store.path.parent);jobs=ImportJobs(library);sessions={}
    app.state.adventures=library;app.state.adventure_jobs=jobs;game.adventures=library
    jobs.resume()
    def review_access(request):
        token=request.cookies.get('solo_adventure_review','')
        if sessions.get(token,0)<time.time():raise HTTPException(403,'Закрытая подготовка не открыта. Подтвердите режим проверки сценария.')
    def image(path,name='material'):
        return FileResponse(path,media_type='image/svg+xml' if path.suffix=='.svg' else 'image/png',headers={'Cache-Control':'private, no-store','Content-Disposition':'inline; filename="'+name+path.suffix+'"','Content-Security-Policy':"default-src 'none'; sandbox"})
    @app.get('/api/adventure/library')
    def books():
        rows=library.list()
        for row in rows:row['metadata']={k:v for k,v in row['metadata'].items() if k in ('series','edition','levels','party_size','setting','rights','related')}
        return rows
    @app.get('/api/adventure/library/{bundle}/modules')
    def modules(bundle:str):return library.modules(bundle)
    @app.get('/api/adventure/library/{bundle}/materials')
    def materials(bundle:str):
        # The inventory of what the importer cut out of the book: kind, page and the
        # printed box of each picture. A candidate is offered here, the picture and
        # the closed text stay behind the review screen until a person confirms it.
        return library.media_index(bundle)
    @app.post('/api/adventure/library')
    def create(body:BundleCreate):return library.create(body.title,body.metadata)
    @app.put('/api/adventure/library/{bundle}')
    def update(bundle:str,body:BundleCreate):return library.update_metadata(bundle,body.title,body.metadata)
    @app.post('/api/adventure/library/{bundle}/files')
    async def upload(bundle:str,request:Request,file:UploadFile=File(...),purpose:str=Form('book')):
        from .source_upload import receive_source
        job=await receive_source(library,jobs,bundle,file,purpose,request)
        return {k:job[k] for k in ('id','status','done','total','message')}
    @app.get('/api/adventure/jobs/{id}')
    def progress(id:str):
        # The dialog that started the import has to say what was understood, and
        # pages alone do not say it. Chapters are the boundaries the importer
        # found, modules the page ranges that were built from them; both are read
        # from the same database the worker writes, so a client never guesses.
        job=library.job(id);data={k:job[k] for k in ('id','status','done','total','message')}
        data['bundle']=job['bundle'];data.update(library.counts(job['bundle'],job['document']))
        return data
    @app.post('/api/adventure/jobs/{id}/cancel')
    def cancel(id:str):
        job=library.job(id)
        if job['status'] in ('queued','running'):library.progress(id,status='cancelled',message='Импорт остановлен. Оригиналы сохранены.')
        return {'status':library.job(id)['status']}
    @app.post('/api/adventure/review/session')
    def open_review(body:ReviewConsent):
        if not body.confirm_spoilers:raise ValueError('Этот режим содержит закрытые материалы Мастера')
        for token in list(sessions):
            if sessions[token]<time.time():sessions.pop(token)
        token=secrets.token_urlsafe(32);sessions[token]=time.time()+900
        response=JSONResponse({'review_open':True,'expires_seconds':900})
        response.set_cookie('solo_adventure_review',token,httponly=True,samesite='strict',max_age=900,path='/api/adventure/review')
        return response
    @app.delete('/api/adventure/review/session')
    def close_review(request:Request):
        sessions.pop(request.cookies.get('solo_adventure_review',''),None);response=JSONResponse({'review_open':False});response.delete_cookie('solo_adventure_review',path='/api/adventure/review');return response
    @app.get('/api/adventure/review/library/{bundle}')
    def review(bundle:str,request:Request):review_access(request);return library.review(bundle)
    @app.put('/api/adventure/review/library/{bundle}/entities')
    async def entity(bundle:str,body:EntityEdit,request:Request):
        # The correction is stored by page and printed name, so a repeated
        # parse applies it again instead of asking the person twice.
        review_access(request)
        async with game.operation:
            return library.edit_entity(bundle,body.module,body.document,body.page,body.entity,body.name,body.value)
    @app.post('/api/adventure/review/library/{bundle}/collection')
    async def collection(bundle:str,body:CollectionSplit,request:Request):
        review_access(request)
        async with game.operation:return library.split_collection(bundle,body.document,body.entries,body.confirmed)
    @app.post('/api/adventure/review/campaigns/{campaign}/continue')
    async def continuation(campaign:str,body:ContinueAdventure,request:Request):
        review_access(request)
        from .adventure_engine import AdventureService
        async with game.operation:
            event=AdventureService(store,library).continue_module(campaign,body.bundle,body.module,body.request_id,body.confirm,body.standalone)
            return {'id':event['id']}
    @app.get('/api/adventure/review/campaigns/{campaign}/journal')
    def journal(campaign:str,request:Request):
        review_access(request);data=store.campaign(campaign)['state'].get('adventure',{})
        return dict(events=data.get('events',{}),journal=data.get('journal',[]),schedule=data.get('schedule',{}),pending_wilderness=data.get('pending_wilderness',[]),pending_hazard=data.get('pending_hazard'))
    @app.post('/api/adventure/review/campaigns/{campaign}/encounter-completion')
    async def encounter_completion(campaign:str,body:EncounterCompletion,request:Request):
        review_access(request)
        if not body.confirmed:raise ValueError('Подтвердите фактическое завершение разыгранной встречи')
        from .adventure_expedition import finish_encounter
        async with game.operation:
            event=store.record_scenario(campaign,body.request_id,'Подтверждение завершения встречи',lambda target:finish_encounter(target,body.encounter))
            return {'id':event['id']}
    @app.get('/api/adventure/review/materials/{id}/text')
    def source_text(id:str,request:Request):
        # What the material itself says: the lines printed in its own box, read by
        # columns; a page material still answers with its whole page.
        review_access(request);return library.material_text(id)
    @app.post('/api/adventure/review/campaigns/{campaign}/attach')
    async def attach(campaign:str,body:AttachAdventure,request:Request):
        review_access(request)
        from .adventure_engine import AdventureService
        from .world import public_results
        async with game.operation:
            event=AdventureService(store,library).attach(campaign,body.bundle,body.request_id,body.confirm,body.module or None)
            return dict(id=event['id'],results=public_results(event['results']))
    @app.get('/api/adventure/review/materials/{id}')
    async def preview(id:str,request:Request,width:int=480):
        review_access(request);library.material(id);path=await asyncio.to_thread(jobs.render,id,width);return image(path)
    @app.put('/api/adventure/review/materials/{id}')
    async def material(id:str,body:MaterialReview,request:Request):
        review_access(request)
        async with game.operation:return library.set_material(id,**body.model_dump())
    @app.get('/api/adventure/review/library/{bundle}/markup')
    def markup_status(bundle: str, request: Request):
        # How much of the module is described in words: what the model read, what
        # it failed to read, and how many materials still answer by name only.
        review_access(request);return library.markup_status(bundle)
    @app.post('/api/adventure/review/library/{bundle}/markup')
    async def markup_batch(bundle: str, body: MaterialMarkupBatch, request: Request):
        # One batch of pictures, bounded: the review screen prepares the module it
        # is about to run, never the whole library at once.
        review_access(request)
        from .material_markup import MaterialMarkup
        marker = MaterialMarkup(library, game.llm, render=jobs.render)
        built, rejected = [], []
        async with game.operation:
            for row in marker.pending(bundle, body.limit):
                try:built.append((await marker.build(row, body.request_id or 'markup-' + row['id'], bundle))['id'])
                except ValueError as exc:rejected.append(dict(id=row['id'], page=row['page'], reason=str(exc)))
        return dict(built=built, rejected=rejected, **library.markup_status(bundle))
    @app.post('/api/adventure/review/materials/{id}/markup')
    async def markup_one(id: str, request: Request):
        # Re-reading one picture after a failure, or after a person fixed the crop.
        review_access(request)
        from .material_markup import MaterialMarkup
        async with game.operation:
            row = await MaterialMarkup(library, game.llm, render=jobs.render).build(id, 'markup-' + id)
        return dict(id=row['id'], page=row['page'], markup=json.loads(row['markup'] or '{}'), note=row['markup_note'])
    @app.put('/api/adventure/review/library/{bundle}/definition')
    async def definition(bundle:str,body:DefinitionReview,request:Request):
        review_access(request)
        async with game.operation:return library.save_definition(bundle,body.definition,body.confirmed).model_dump()
    from .adventure_analysis import AnalysisRequest,AdventureAnalysis
    class LocalIndex(Strict):
        document:str=Field(pattern=r'^[a-f0-9]{64}$')
        pages:list[int] | None=Field(default=None,max_length=2000)
    @app.post('/api/adventure/review/library/{bundle}/index')
    async def local_index(bundle:str,body:LocalIndex,request:Request):
        review_access(request)
        with library.db() as db:belongs=db.execute('SELECT 1 FROM bundle_files WHERE bundle=? AND document=?',(bundle,body.document)).fetchone()
        if not belongs:raise ValueError('Источник не принадлежит комплекту')
        from .pdf_pipeline import PDFPipeline
        async with game.operation:
            index=PDFPipeline(library)
            result=await asyncio.to_thread(index.index,body.document,body.pages)
            return dict(**result,summary=index.summary(body.document),paid_requests=0)
    @app.post('/api/adventure/review/library/{bundle}/analysis')
    async def analyze(bundle:str,body:AnalysisRequest,request:Request):
        review_access(request)
        if not hasattr(game.llm,'configured'):raise ValueError('Облачный адаптер не настроен')
        async with game.operation:return await AdventureAnalysis(library,game.llm).analyze(bundle,body)
    @app.get('/api/campaigns/{campaign}/adventure/materials/{id}')
    async def player_material(campaign:str,id:str,width:int=1000):
        state=store.campaign(campaign)['state'];adventure=state.get('adventure',{})
        if id not in adventure.get('known_media',[]):raise HTTPException(404,'Материал недоступен герою')
        material=library.material(id)
        if material['scope']=='gm' or not material['reviewed'] or not material['sanitized']:raise HTTPException(404,'Материал недоступен герою')
        owner=adventure.get('media_bundles',{}).get(id,adventure['bundle'])
        documents={d['id'] for d in library.review(owner)['documents']}
        if material['document'] not in documents:raise HTTPException(404,'Материал недоступен герою')
        return image(await asyncio.to_thread(jobs.render,id,width))
