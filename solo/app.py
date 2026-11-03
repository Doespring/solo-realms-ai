import asyncio
import json
import os
import time
from contextlib import asynccontextmanager, aclosing
from pathlib import Path
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field,SecretStr
from fastapi.exceptions import RequestValidationError
from .contracts import Strict, Hero, Master, Plan, Step
from .selection import Selection
from .version import VERSION
from .storage import Store, dump
from .llm import CloudLLM,CloudUnavailable,BudgetExceeded
from .cloud_settings import CloudSettings,CloudProfile
from .cloud_cost import CostLedger
from .game import Game
from .profiles import RISK_LIMITATION

ROOT = Path(__file__).resolve().parents[1]

class NewCampaign(Strict):
    name: str = Field(min_length=1,max_length=100)
    hero: Hero
    ruleset: str = Field(default='dnd2024',max_length=80)
    world_mode: str = Field(default='story',pattern='^(story|sandbox|mixed)$')
    map_travel: bool = False
class ActionRequest(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    text: str = Field(min_length=1,max_length=4000)
    mode: str = Field(default='action',pattern='^(action|dialogue|gm)$')
    target: str = Field(default='',max_length=120)
    step: Step | None = None
class PlayRequest(Strict):
    request_id: str = Field(min_length=1,max_length=80)
    text: str = Field(min_length=1,max_length=4000)
    selection: Selection | None = None

class CommandTurnRequest(Strict):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_.-]{1,80}$')
    text: str = Field(min_length=1, max_length=4000)
    expected_version: int = Field(ge=0, strict=True)
class SaveRequest(Strict):
    name: str = Field(min_length=1,max_length=100)
class NarrativeRequest(Strict):
    regenerate: bool = False
class NarrativeCorrection(Strict):
    text: str = Field(min_length=1,max_length=12000)
class ProposalDecision(Strict):
    approve: bool
class ProfileRequest(Strict):
    name: str = Field(min_length=1,max_length=100)
    master: Master

class ApiKeyRequest(Strict):
    api_key: SecretStr=Field(min_length=1,max_length=4096)
class ConfirmBudget(Strict):
    confirm: bool

def create_app(db=None,llm=None,*,local_port=8000,experimental=False,offline_mock=False):
    from .prototype_profile import application_database
    store = Store(application_database(db))
    cloud_settings=getattr(llm,'settings',None) or CloudSettings(store.path.parent/'cloud-profiles.json')
    costs=getattr(llm,'ledger',None) or CostLedger(store.path.parent/'cloud-usage.sqlite3')
    game = Game(store,llm or CloudLLM(cloud_settings,costs))
    readiness={'status':'initializing'}
    @asynccontextmanager
    async def lifespan(app):
        async def initialize():
            try:
                async with game.operation:
                    readiness.update(await game.llm.warmup() if hasattr(game.llm,'warmup') else {'status':'ready'})
            except Exception as exc:readiness.update(status='unavailable',error=str(exc))
        # The start identity must be taken AT START. Frozen lazily on the first
        # /api/health it described whatever the disk held by then: an edit made
        # before anyone asked became the baseline, so the very change the check
        # exists to catch reported drift=False for the life of the process.
        from .build_identity import build_identity as _identity
        await asyncio.to_thread(_identity)
        task=asyncio.create_task(initialize())
        yield
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        if hasattr(app.state,'preparation_pipeline'):await app.state.preparation_pipeline.close()
        if hasattr(app.state,'adventure_jobs'):app.state.adventure_jobs.close()
        if hasattr(game.llm,'close'):await game.llm.close()
    app = FastAPI(title='Solo Realms AI',docs_url=None,redoc_url=None,lifespan=lifespan)
    app.state.store,app.state.game = store,game
    app.state.cloud_settings,app.state.costs=cloud_settings,costs
    app.state.desktop=None
    app.state.local_port=local_port
    app.state.experimental=experimental
    app.state.offline_mock=offline_mock

    @app.post('/api/campaigns/{campaign}/command-turn')
    async def command_turn(campaign: str, body: CommandTurnRequest):
        return await game.play_command(campaign, body.request_id, body.text, body.expected_version)

    from .tabletop_api import register_tabletop_api
    register_tabletop_api(app,store,game)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])

    @app.middleware('http')
    async def local_only(request,call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET','HEAD') and origin and origin not in (f'http://127.0.0.1:{local_port}',f'http://localhost:{local_port}','http://testserver'):
            return JSONResponse({'detail':'Чужой Origin не допускается'},status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(KeyError)
    async def missing(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=404)
    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=409)

    @app.exception_handler(BudgetExceeded)
    async def budget(request,exc):
        return JSONResponse({'detail':str(exc),'code':'budget_exceeded','approval':exc.approval,'quote':exc.quote,'currency':exc.currency},status_code=409)
    @app.exception_handler(CloudUnavailable)
    async def unavailable(request,exc):return JSONResponse({'detail':str(exc)},status_code=503)
    @app.exception_handler(RequestValidationError)
    async def validation(request,exc):
        # Never echo rejected input values, including a submitted API key.
        return JSONResponse({'detail':[{'loc':e['loc'],'type':e['type'],'msg':e['msg']} for e in exc.errors()]},status_code=422)

    @app.get('/')
    def index():
        if experimental:
            from fastapi.responses import HTMLResponse
            mode='OFFLINE MOCK · без AI-запросов' if offline_mock else 'Облачный Мастер · подключение и расходы включаются вами'
            html=(ROOT/'web/index.html').read_text(encoding='utf-8').replace('<body>',
                '<body><div role="status" class="notice">ЭКСПЕРИМЕНТАЛЬНЫЙ ПРОТОТИП · '+mode+'</div>')
            return HTMLResponse(html)
        return FileResponse(ROOT/'web/index.html')
    from .navigation import register_document_api
    register_document_api(app,ROOT)
    app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')

    @app.get('/api/health')
    async def health():
        available=await game.llm.available()
        status='unconfigured' if not available else ('ready' if getattr(game.llm,'last_generation',None) else 'configured')
        from .build_identity import build_identity
        # build_identity() shells out to git; running it here would block the event
        # loop and starve every other request until git returns.
        # refresh re-reads the files that are actually loaded and costs under a
        # millisecond, so the answer can say whether the disk has moved on. The
        # cached form could only ever describe the moment the process started,
        # which is exactly how an old process passes itself off as current.
        identity=await asyncio.to_thread(build_identity, refresh=True)
        return dict(product='Solo Realms AI',llm=available,version=VERSION,offline=offline_mock,cloud_only=not offline_mock,
                    experimental=experimental,build=identity['build'],commit=identity['commit'],
                    # The moment this process took its identity. Without it a
                    # reader cannot tell a fresh server from one that has been
                    # running since before their edit - the very confusion
                    # section 9.4 asks the build stamp to prevent.
                    started=identity.get('started'),covered=identity.get('covered'),
                    modules=identity.get('modules'),
                    drift=identity.get('drift'),disk=identity.get('disk'),
                    ui=identity.get('ui'),ui_drift=identity.get('ui_drift'),
                    # Which layer diverged. Without this the page can only say
                    # "the code on disk changed", which blames the server even
                    # when only web/* was edited and the server is running its
                    # own, still-current code.
                    ui_disk=identity.get('ui_disk'),
                    # The interface contract version, so a client can refuse to
                    # speak to a server it does not understand instead of
                    # failing later on a field that silently changed shape.
                    contract=identity['contract'],
                    readiness={**readiness,'status':status},risk_limitation=RISK_LIMITATION)

    @app.get('/api/desktop/status')
    def desktop_status():return app.state.desktop.status() if app.state.desktop else {'available':False}

    @app.get('/api/ai/profiles')
    def ai_profiles():return cloud_settings.public()
    from .cloud_setup import SetupCloud
    @app.post('/api/ai/setup')
    async def ai_setup(body:SetupCloud):
        from .cloud_setup import setup
        if not isinstance(game.llm,CloudLLM):raise ValueError('Подключение недоступно для тестового адаптера')
        async with game.operation:return await setup(game.llm,body)
    @app.get('/api/ai/groq-preset')
    def groq_preset():
        from .groq import preset,CANDIDATES
        return dict(profile=preset().model_dump(mode='json'),candidates=CANDIDATES,verification='documentation_only',account_models_verified=False)
    @app.get('/api/ai/routerai-preset')
    def routerai_preset():
        from .ai_roles import routerai_preset
        return dict(profile=routerai_preset().model_dump(mode='json'),verification='public_catalog_only',account_models_verified=False,generation_tested=False)
    @app.get('/api/ai/quota')
    def quota():return cloud_settings.quota
    @app.post('/api/ai/profiles')
    async def ai_save(body:CloudProfile):
        async with game.operation:
            previous=cloud_settings.profiles.get(body.id)
            if body.enabled and (previous is None or not previous.enabled):raise ValueError('Используйте «Включить облачный AI» и подтвердите разовый лимит расходов.')
            if body.total_budget!=(previous.total_budget if previous else None):raise ValueError('Общий лимит меняется только через явное подтверждение включения AI.')
            profile_id=cloud_settings.save(body)
            return dict(id=profile_id,key_present=cloud_settings.vault.exists(profile_id))
    @app.get('/api/ai/profiles/{profile}/status')
    def ai_profile_status(profile:str):
        from .cloud_control import status
        return status(cloud_settings,profile,costs)
    from .cloud_control import EnableCloud
    @app.post('/api/ai/profiles/{profile}/enable')
    async def ai_enable(profile:str,body:EnableCloud):
        from .cloud_control import enable
        async with game.operation:return enable(cloud_settings,costs,profile,body,getattr(game.llm,'session',None))
    @app.post('/api/ai/profiles/{profile}/activate')
    async def ai_activate(profile:str):
        async with game.operation:cloud_settings.activate(profile)
        return {'active':profile}
    @app.delete('/api/ai/profiles/{profile}')
    async def ai_delete(profile:str):
        async with game.operation:cloud_settings.delete(profile)
        return {'deleted':True}
    @app.put('/api/ai/profiles/{profile}/key')
    async def ai_key(profile:str,body:ApiKeyRequest):
        async with game.operation:
            cloud_settings.profile(profile);cloud_settings.vault.set(profile,body.api_key.get_secret_value())
        return {'key_present':True}
    @app.delete('/api/ai/profiles/{profile}/key')
    async def ai_delete_key(profile:str):
        async with game.operation:cloud_settings.profile(profile);cloud_settings.vault.delete(profile)
        return {'key_present':False}
    @app.get('/api/ai/profiles/{profile}/models')
    async def ai_models(profile:str):
        if not isinstance(game.llm,CloudLLM):raise ValueError('Каталог недоступен для тестового адаптера')
        return await game.llm.models(profile)
    @app.get('/api/ai/profiles/{profile}/endpoints/{role}')
    async def ai_endpoints(profile:str,role:str):
        if not isinstance(game.llm,CloudLLM):raise ValueError('Каталог недоступен для тестового адаптера')
        return await game.llm.endpoints(profile,role)
    @app.post('/api/ai/profiles/{profile}/probe')
    async def ai_probe(profile:str):
        async with game.operation:
            if cloud_settings.active!=profile:raise ValueError('Сначала активируйте профиль')
            from .ai_roles import select_role
            cfg,_=select_role(cloud_settings.profile(profile),'connection')
            bound=1024 if cfg.thinking in ('low','medium') else 64
            text=''.join([c async for c in game.llm.stream('Ответь по-русски одним словом.','Готов?',mode='connection',max_tokens=bound,temperature=.05)])
            return {'text':text,'generation_tested':True,'metric':game.llm.metrics[-1]}
    @app.get('/api/ai/costs')
    def ai_costs(campaign:str|None=None):
        result=costs.summary(campaign,getattr(game.llm,'session',None))
        result['limits']=costs.quotas(cloud_settings.profile(),getattr(game.llm,'session',None)) if cloud_settings.active else []
        return result
    @app.post('/api/ai/budgets/{approval}/approve')
    async def ai_approve(approval:str,body:ConfirmBudget):
        if not body.confirm:raise ValueError('Нужно явное подтверждение')
        costs.approve(approval);return {'approved':approval,'single_use':True}

    @app.get('/api/campaigns')
    def campaigns():
        return store.list_campaigns()
    @app.post('/api/campaigns')
    async def create(body:NewCampaign):
        async with game.operation:
            from .rulesets import RulesetRepository,RulesetEngine
            preset=RulesetRepository(store).get(body.ruleset);effects=RulesetEngine(preset).exhaustion_effects(body.hero)
            if effects['mechanical_death'] or body.hero.hp>effects['effective_max_hp']:raise ValueError('Хиты и истощение не соответствуют выбранным правилам')
            from .world import WorldSettings
            return store.create(body.name,body.hero,preset,WorldSettings(mode=body.world_mode,map_travel=body.map_travel))
    @app.get('/api/campaigns/{campaign}')
    def state(campaign:str):
        current = store.campaign(campaign)
        # A stored master that is not an object used to raise here and turned
        # the whole campaign into a 500: the player saw a broken product instead
        # of a scene. A damaged record is reported as missing, not as a crash;
        # resetting it to defaults also keeps saving works, so play can go on.
        stored_master=current['master']
        if not isinstance(stored_master,dict):
            current['master']=Master().model_dump()
        from .combat_view import public_campaign
        history=store.history(campaign);conversation=store.conversation_history(campaign)
        # One accepted turn is one public exchange. A conversational turn is
        # written twice by design: an event in the journal (with its narrative)
        # and a conversation row linked to that event by context.journal_id.
        # Both are true records, so nothing is deleted; the projection marks the
        # linked row with 'journal_event' so the reader renders one pair instead
        # of two. Two identical-sounding player lines remain two turns because
        # their journals and request ids differ.
        journals={str(r.get('id')) for r in history}
        for row in conversation:
            context=row.get('context')
            # The column stores JSON text, so a reader that expects a dict sees
            # nothing and silently keeps the duplicate pair on screen.
            if isinstance(context,str):
                try:context=json.loads(context)
                except ValueError:context=None
            link=context.get('journal_id') if isinstance(context,dict) else None
            if link and str(link) in journals:row['journal_event']=str(link)
        # The reader needs to know whether older turns exist and how to ask for
        # them; without this the page simply ended at fifty.
        total=store.conversation_count(campaign)
        more=len(conversation)<total
        # Which calculator answers mechanical commands. A campaign created before
        # the engine existed resolves them in the plain Python service, and the
        # table deserves to know that instead of believing the fight followed the
        # printed rules. Section 7.1: no silent fallback - naming it is the fix
        # that does not change any outcome.
        rules_backend='nat20' if 'nat20' in current['state'] else 'legacy'
        # public_campaign() copies the state; the campaign record also carries
        # the master's own settings. The table needs one flag from them and none
        # of the prose: system_prompt and boundaries are the owner's, and the
        # boundaries name what must not be described. Section 4.
        master=current.get('master') or {}
        public=public_campaign(current)
        public['master']=dict(hide_mechanics=bool(master.get('hide_mechanics')))
        return dict(**public,conversation_cursor=store.conversation_cursor(conversation),
                    conversation_total=total,conversation_more=more,rules_backend=rules_backend,
                    rules_backend_note=('' if rules_backend=='nat20' else
                        'Механика этой кампании считается упрощённым калькулятором: движок Nat20 не подключён.'),
                    history=history,pending=store.pending(campaign),saves=store.saves(campaign),gm_history=store.gm_history(campaign),conversation=conversation)

    @app.get('/api/campaigns/{campaign}/conversation')
    def conversation_page(campaign:str,before:str='',limit:int=50):
        """Older dialogue, addressed by the cursor the campaign response returned."""
        store.campaign(campaign)
        cursor=None
        if before:
            try:
                # The cursor goes out as a JSON pair and comes back as the
                # same pair; json.loads on a list cannot be unpacked into a
                # tuple by plain assignment, which made every second page 400.
                pair=json.loads(before)
                if not isinstance(pair,(list,tuple)) or len(pair)!=2: raise ValueError
                cursor=(float(pair[0]),str(pair[1]))
            except (ValueError,TypeError,KeyError):raise HTTPException(400,'Некорректный курсор истории.')
        limit=max(1,min(int(limit),200))
        rows=store.conversation_history(campaign,limit=limit,before=cursor)
        # 'context' is the server's own bookkeeping and stays here. 'journal_event'
        # must go out: the screen merges the dialogue with the journal by it, so
        # without the field an older page is drawn a second time beside the event
        # that already shows the same turn. The field name is the public link,
        # not the internal record.
        public=[{k:row[k] for k in ('id','request_id','original','reply','kind','created') if k in row}
                for row in rows]
        # Same rule as the campaign response: the row points at its journal event
        # through context.journal_id, and the link only counts when the event is
        # in the journal the page already shows.
        journals={str(r.get('id')) for r in store.history(campaign,500)}
        for row,raw in zip(public,rows):
            context=raw.get('context')
            if isinstance(context,str):
                try:context=json.loads(context)
                except ValueError:context=None
            link=context.get('journal_id') if isinstance(context,dict) else None
            if link and str(link) in journals:row['journal_event']=str(link)
        return dict(conversation=public,conversation_cursor=store.conversation_cursor(rows),
                    conversation_more=len(rows)==limit)

    @app.post('/api/campaigns/{campaign}/play')
    async def play(campaign:str,body:PlayRequest):
        return await game.play(campaign,body)

    @app.post('/api/campaigns/{campaign}/actions')
    async def action(campaign:str,body:ActionRequest):
        try:
            return await game.route(campaign,body)
        except Exception as exc:
            if isinstance(exc,(ValueError,KeyError)):
                raise
            raise HTTPException(503,'Облачный Мастер недоступен или выдал неверный JSON: '+str(exc)) from exc

    @app.put('/api/actions/{action}')
    async def edit(action:str,body:Plan):
        async with game.operation:
            return store.edit(action,body)
    @app.post('/api/actions/{action}/cancel')
    async def cancel(action:str):
        async with game.operation:
            store.cancel(action)
            return {'ok':True}
    @app.post('/api/actions/{action}/confirm')
    async def confirm(action:str):
        async with game.operation:
            return game.public_event(store.apply(action))

    @app.post('/api/events/{event}/narrate')
    async def narrate(event:str,body:NarrativeRequest):
        store.event(event)
        async def stream():
            try:
                async with aclosing(game.narrate(event,body.regenerate)) as stream:
                    async for data in stream:
                        yield 'data: '+dump(data)+'\n\n'
            except Exception as exc:
                yield 'data: '+dump({'type':'error','message':str(exc)})+'\n\n'
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

    @app.put('/api/events/{event}/narrative')
    async def correct(event:str,body:NarrativeCorrection):
        async with game.operation:return game.public_event(store.edit_narrative(event,body.text))

    @app.post('/api/events/{event}/proposals/{proposal}')
    async def proposal(event:str,proposal:str,body:ProposalDecision):
        async with game.operation:
            result=store.decide_proposal(event,proposal,body.approve)
            return {'event':game.public_event(result) if result else None}

    @app.post('/api/gm/{message}/stream')
    async def gm_stream(message:str):
        store.gm_message(message)
        async def stream():
            async with aclosing(game.gm_answer(message)) as stream:
                async for data in stream:yield 'data: '+dump(data)+'\n\n'
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})

    @app.get('/api/master/profiles')
    def profiles():return [{**p,'master':Master.model_validate(p['master']).model_dump()} for p in store.profiles()]

    @app.post('/api/master/profiles')
    async def add_profile(body:ProfileRequest):
        async with game.operation:return {'id':store.create_profile(body.name,body.master)}

    @app.put('/api/master/profiles/{profile}')
    async def edit_profile(profile:str,body:ProfileRequest):
        async with game.operation:store.change_profile(profile,body.name,body.master)
        return {'id':profile}

    @app.delete('/api/master/profiles/{profile}')
    async def delete_profile(profile:str):
        async with game.operation:store.change_profile(profile,delete=True)
        return {'deleted':profile}

    @app.get('/api/master/profiles/{profile}/history')
    def profile_history(profile:str):return store.profile_history(profile)

    @app.post('/api/campaigns/{campaign}/undo')
    async def undo(campaign:str):
        async with game.operation:
            from .combat_view import public_campaign
            return public_campaign(store.undo(campaign))
    @app.post('/api/campaigns/{campaign}/saves')
    async def save(campaign:str,body:SaveRequest):
        async with game.operation:
            return {'id':store.save(campaign,body.name)}
    @app.post('/api/campaigns/{campaign}/restore/{checkpoint}')
    async def restore(campaign:str,checkpoint:str):
        async with game.operation:
            store.backup()
            from .combat_view import public_campaign
            return public_campaign(store.restore(campaign,checkpoint))

    @app.get('/api/campaigns/{campaign}/master/history')
    def master_history(campaign:str):
        return store.master_history(campaign)
    @app.put('/api/campaigns/{campaign}/master')
    async def master(campaign:str,body:Master):
        async with game.operation:
            store.master(campaign,body)
            return body
    @app.get('/api/events/{event}/prompt')
    def prompt(event:str):
        e=store.event(event)
        c=store.campaign(e['campaign'])
        c['state']=e['after_state']
        c['version']='event:'+event
        system,packet,master=game.compiler.narrator(c,e)
        return dict(system=system,packet=json.loads(packet),master=master.model_dump())

    @app.get('/api/performance')
    async def performance():
        process=psutil.Process()
        result=dict(cpu_percent=psutil.cpu_percent(),ram_available_mb=round(psutil.virtual_memory().available/1048576),
                    app_ram_mb=round(process.memory_info().rss/1048576),llm_calls=game.llm.metrics[-30:],
                    map_fps=None)
        result['runtime_ram_mb']=None;result['gpu']=None;result['cloud_only']=True
        result['costs']=costs.summary(session=getattr(game.llm,'session',None))['session']
        return result

    from .rules_api import register_rules_api
    register_rules_api(app,store,game)
    from .hybrid_rules_api import register_hybrid_rules_api
    register_hybrid_rules_api(app,store,game)
    from .combat_api import register_combat_api
    register_combat_api(app,store,game)
    from .inventory_api import register_inventory_api
    register_inventory_api(app,store,game)
    from .world_api import register_world_api
    register_world_api(app,store,game)
    from .visual_assets import register_visual_api
    register_visual_api(app,ROOT)
    from .data_management import register_data_api
    register_data_api(app,store,game)
    from .preparation import register_preparation_api
    register_preparation_api(app,store,game)
    from .adventure_api import register_adventure_api
    register_adventure_api(app,store,game)
    from .creation import register_creation_api
    register_creation_api(app,store,game)
    # Второй редактор (solo/character_creation.py) отключён по заданию
    # «единый путь»: у героя ровно один источник правил — мастер Nat20
    # (/api/prototype/options). Код сохранён намеренно, чтобы отключение было
    # обратимым; удаление файлов остаётся за владельцем.
    from .chronicles import register_chronicles_api
    register_chronicles_api(app,store)
    @app.get('/api/rules/knowledge')
    def rule_sources():return dict(sources=game.compiler.rule_knowledge.catalog(),private_index=True,model_calls=0)
    @app.get('/api/rules/knowledge/search')
    def rule_search(query:str,ruleset:str='dnd2024'):
        return game.compiler.rule_knowledge.search(query,ruleset)
    from .play_screen import register_play_screen_api
    register_play_screen_api(app,store,game)
    from .playable_ui import register_playable_ui_api
    register_playable_ui_api(app,store,game)
    if experimental:
        from importlib import import_module, util
        components={}
        for module_name, registration in (('solo.prototype_ui','register_prototype_ui_api'),
                                           ('solo.game','register_prototype_gm_api')):
            if util.find_spec(module_name) is None:
                components[registration]='pending'
                continue
            register=getattr(import_module(module_name),registration,None)
            if not callable(register):
                components[registration]='pending'
                continue
            if registration=='register_prototype_gm_api':
                register(app,store,game,preparation=getattr(app.state,'preparation',None))
            else:register(app,store,game)
            components[registration]='registered'
        app.state.prototype_components=components
        @app.get('/api/prototype/status')
        def prototype_status():
            return dict(experimental=True,offline_mock=offline_mock,components=components,
                        preserved_data_incident='open',live_ai_tested=False)
    from .upload_limit import UploadSizeMiddleware
    app.add_middleware(UploadSizeMiddleware)
    return app

# Imports without an explicitly selected profile must never open ROOT/data.
app = create_app() if os.environ.get('SOLO_TEST')!='1' and os.environ.get('SOLO_DATA_DIR') else None
