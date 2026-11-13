import asyncio
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager, aclosing
from typing import Literal
from pathlib import Path
import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field,SecretStr
from fastapi.exceptions import RequestValidationError
from .contracts import Strict, Hero, Master, Plan, Step
from .selection import Selection
from .version import VERSION
from .storage import Store, dump
from .llm import CloudLLM,CloudUnavailable,BudgetExceeded
from .cloud_settings import CloudSettings,CloudProfile,KeyVaultError,select_vault
from .cloud_cost import CostLedger
from .game import Game
from .profiles import RISK_LIMITATION

ROOT = Path(__file__).resolve().parents[1]

# The chat-only shell (?ui=chat) is one screen: the story feed and the composer.
# The panel scripts are not loaded at all, so their panels never reach the DOM,
# and the markup between the cut markers in web/index.html is dropped. The list
# lives here only: a copy inside the test would drift away from the application.
# wizard.js is the hero editor itself: the chat shell has no rules panel and
# no second copy of the catalogues, so without it the second step of the one
# entry dead-ends. Dropping it makes «Собрать героя» find no window.soloWizard.
CHAT_ONLY_SCRIPTS = ('play.js', 'wizard.js')
CHAT_ONLY_CUT = ('<!-- chat-only:cut -->', '<!-- /chat-only:cut -->')
SCRIPT_SRC = re.compile(r'<script\b[^>]*\bsrc="([^"]+)"[^>]*>\s*</script>\s*', re.I)


def chat_only_shell(html: str) -> str:
    """Reduce web/index.html to the single chat screen served for ?ui=chat."""
    start, end = CHAT_ONLY_CUT
    html = re.sub(re.escape(start) + r'.*?' + re.escape(end) + r'\s*', '', html, flags=re.S)
    def kept(match):
        name = match.group(1).split('?')[0].rsplit('/', 1)[-1]
        return match.group(0) if name in CHAT_ONLY_SCRIPTS else ''
    html = SCRIPT_SRC.sub(kept, html)
    return re.sub(r'<html\b', '<html data-ui="chat"', html, count=1)

class NewCampaign(Strict):
    """r210: единственный калькулятор — Nat20.

    Поля ``hero``, ``ruleset`` и мира остались в контракте, чтобы старый
    клиент получил причину отказа с текстом, а не 422 на незнакомое поле.
    Они больше не читаются: лист героя считает движок Nat20, а не ручная
    форма. Кампания без явной сцены Nat20 не создаётся вовсе.
    """
    name: str = Field(min_length=1,max_length=100)
    scene: Literal['lantern-post'] | None = None
    hero: Hero | None = None
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

logger=logging.getLogger('solo.app')

# Человеческие имена полей и причин для отказа формы. Английский `msg` pydantic
# уходит игроку как есть только там, где своего перевода нет.
_FIELD_TITLES={'name':'имя героя','draft':'черновик героя','answers':'ответы шагов',
    'build':'лист героя','hero':'герой','text':'текст','adventure':'приключение',
    'request_id':'номер запроса','prepared_spells':'заклинания','ability_scores':'характеристики',
    'selected_choices':'выборы класса','backstory':'предыстория','background_slug':'предыстория',
    # n02: профиль мастера — тоже форма; игроку нужен темп, а не путь в JSON.
    'master.literary.rhythm':'темп повествования','master.literary':'художественный стиль',
    'master.literary.detail':'подробность','master.literary.emotion':'эмоциональность',
    'master.literary.dialogue':'диалоги','master.literary.imagery':'образность',
    'master.literary.initiative':'инициативность подачи','master.literary.surprises':'сюжетные неожиданности',
    'master.literary.freedom':'фокус мира','master.literary.tension':'атмосфера',
    'master.literary.pace':'темп','master.literary.npc_autonomy':'самостоятельность NPC',
    'master.literary.focus':'акцент','master.literary.consequences':'подача последствий',
    'master.profile_name':'имя профиля'}
_VALIDATION_TEXTS={'string_too_short':'пустое или слишком короткое значение',
    'string_too_long':'слишком длинный текст','missing':'поле не передано',
    'dict_type':'ожидается набор «ключ → значение»','list_type':'ожидается список',
    'int_parsing':'нужно целое число','int_type':'нужно целое число',
    'float_parsing':'нужно число','literal_error':'недопустимое значение',
    'bool_parsing':'нужно «да» или «нет»','extra_forbidden':'лишнее поле',
    'string_type':'ожидается текст','value_error':'значение не принято'}


def validation_message(rows):
    """Русская причина отказа формы, а не общее «не удалось обработать запрос»."""
    parts=[]
    for row in list(rows or ())[:3]:
        loc=[str(part) for part in (row.get('loc') or ()) if part not in ('body','query','path')]
        field='.'.join(loc) or 'запрос'
        named=_FIELD_TITLES.get(field) or _FIELD_TITLES.get(field.split('.')[0]) or field
        parts.append(named+' — '+_VALIDATION_TEXTS.get(row.get('type'),row.get('msg') or 'значение не принято'))
    return ('Сервер не принял запрос: '+'; '.join(parts)+'.') if parts else 'Сервер не принял запрос: неверная форма тела.'


def create_app(db=None,llm=None,*,local_port=8000,experimental=False,offline_mock=False):
    from .prototype_profile import application_database
    store = Store(application_database(db))
    cloud_settings=getattr(llm,'settings',None) or CloudSettings(store.path.parent/'cloud-profiles.json',select_vault(store.path.parent/'cloud-secrets'))
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
        task=asyncio.create_task(initialize())
        # r238: разбор книги не ждёт вкладку. Прогон живёт в базе, поэтому прошлый
        # процесс мог умереть посреди разбора: на старте всё, у чего есть остаток,
        # продолжается само, а дальше за брошенным следит сторож (`tick` раз в 60 с).
        # Прогон, который сам не поднялся, сторож поднимет на первом же проходе.
        pipeline=getattr(app.state,'preparation_pipeline',None)
        watchdog=boot=None
        if pipeline is not None and not offline_mock:
            from .preparation_watchdog import PreparationWatchdog
            try:interval=float(os.environ.get('SOLO_PREPARATION_WATCHDOG','60') or 60)
            except ValueError:interval=60.0
            if interval>0:
                watchdog=PreparationWatchdog(pipeline,interval=interval,
                    log_path=store.path.parent/'preparation-watchdog.log')
                app.state.preparation_watchdog=watchdog
            async def boot_work():
                try:
                    raised=await pipeline.resume_pending()
                    if raised:logger.warning('r238: поднял после перезапуска %s прогон(ов) подготовки: %s',len(raised),', '.join(raised))
                except Exception as exc:logger.warning('r238: подъём подготовки после перезапуска не удался: %s',exc)
                if watchdog is not None:await watchdog.start()
            boot=asyncio.create_task(boot_work())
        yield
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        if boot is not None:
            boot.cancel()
            try:await boot
            except asyncio.CancelledError:pass
        if watchdog is not None:await watchdog.stop()
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

    # r250: журнал действий владельца — что нажал, что запросил, что ответил сервер.
    from .telemetry import install as install_actions
    install_actions(app, Path(os.environ['SOLO_ACTIONS_LOG']) if os.environ.get('SOLO_ACTIONS_LOG')
                    else ROOT/'runtime'/'web-local'/'actions.jsonl')


    @app.middleware('http')
    async def local_only(request,call_next):
        origin = request.headers.get('origin')
        if request.method not in ('GET','HEAD') and origin and origin not in (f'http://127.0.0.1:{local_port}',f'http://localhost:{local_port}','http://testserver'):
            # n02: у отказа есть машиночитаемый код и русская причина — экран печатает
            # message, а код уходит в журнал. Голый текст читался как «не удалось».
            return JSONResponse({'detail':{'code':'foreign_origin','message':
                'Запрос пришёл с чужого адреса и не принят. Откройте приложение по адресу 127.0.0.1 и повторите.'}},status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(KeyError)
    async def missing(request,exc):
        # str(KeyError) оборачивает текст в кавычки: игрок читал «'Кампания не
        # найдена'». Причина называется как есть, без обёртки.
        return JSONResponse({'detail':str(exc.args[0]) if exc.args else 'Не найдено'},status_code=404)
    @app.exception_handler(ValueError)
    async def invalid(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=409)

    @app.exception_handler(BudgetExceeded)
    async def budget(request,exc):
        return JSONResponse({'detail':str(exc),'code':'budget_exceeded','approval':exc.approval,'quote':exc.quote,'currency':exc.currency},status_code=409)
    @app.exception_handler(CloudUnavailable)
    async def unavailable(request,exc):return JSONResponse({'detail':str(exc)},status_code=503)
    @app.exception_handler(KeyVaultError)
    async def key_vault(request,exc):
        # Отказ хранилища — не 500 и не трейсбек: человеку нужен русский текст и что делать.
        return JSONResponse({'detail':str(exc)},status_code=409)
    @app.exception_handler(RequestValidationError)
    async def validation(request,exc):
        # Never echo rejected input values, including a submitted API key.
        rows=[{'loc':e['loc'],'type':e['type'],'msg':e['msg']} for e in exc.errors()]
        # Отказ формы раньше уходил только списком pydantic: ни в журнале, ни на экране
        # причины не было. Теперь поля и тип есть в журнале, а рядом с `detail` (форма
        # списка не изменилась) лежит русская `message`, которую читает клиент.
        logger.warning('api: форма запроса отклонена %s %s: %s',request.method,request.url.path,
            '; '.join(f"{'.'.join(str(part) for part in row['loc'])} {row['type']}" for row in rows[:5]))
        return JSONResponse({'detail':rows,'message':validation_message(rows)},status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        # 500 без записи в журнале оставлял игрока с «Не удалось обработать запрос» и без
        # причины: приложение логировало маршруты почти никак. Любая необработанная
        # ошибка оставляет в журнале тип, маршрут и трейсбек.
        logger.exception('api: необработанная ошибка %s %s',request.method,request.url.path)
        return JSONResponse({'detail':{'code':'internal','message':
            'Внутренняя ошибка приложения: действие не выполнено. Повторите; если повторяется — сообщите время и шаг.'}},status_code=500)

    @app.get('/')
    def index(request: Request):
        page=ROOT/'web/index.html'
        if request.query_params.get('ui')=='chat':
            return HTMLResponse(chat_only_shell(page.read_text(encoding='utf-8')))
        if experimental:
            mode='OFFLINE MOCK · без AI-запросов' if offline_mock else 'Облачный Мастер · подключение и расходы включаются вами'
            html=page.read_text(encoding='utf-8').replace('<body>',
                '<body><div role="status" class="notice">ЭКСПЕРИМЕНТАЛЬНЫЙ ПРОТОТИП · '+mode+'</div>')
            return HTMLResponse(html)
        return FileResponse(page)
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
        identity=await asyncio.to_thread(build_identity)
        return dict(product='Solo Realms AI',llm=available,version=VERSION,offline=offline_mock,cloud_only=not offline_mock,
                    experimental=experimental,build=identity['build'],commit=identity['commit'],
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
        from .cloud_settings import KNOWN_PROVIDERS,verify_public_address
        async with game.operation:
            previous=cloud_settings.profiles.get(body.id)
            if body.enabled and (previous is None or not previous.enabled):raise ValueError('Используйте «Включить облачный AI» и подтвердите разовый лимит расходов.')
            if body.total_budget!=(previous.total_budget if previous else None):raise ValueError('Общий лимит меняется только через явное подтверждение включения AI.')
            # Кому достанется ключ, решают провайдер и адрес, а не модель: смена
            # модели — тот же сервер. Изменился получатель — старый ключ больше
            # не действует, профиль выключен, включение заново требует лимита.
            changed=previous is not None and previous.delivery_target()!=body.delivery_target()
            if previous is None or changed:
                # getaddrinfo блокирующий: в потоке, иначе держит событийный цикл.
                await asyncio.to_thread(verify_public_address,body.base_url)
            custom=body.provider.strip().lower() not in KNOWN_PROVIDERS and (previous is None or changed)
            notice=None
            if changed:
                body=body.model_copy(update=dict(enabled=False))
                notice=('Провайдер или адрес изменены: профиль выключен, сохранённый ключ удалён. '
                        'Включите облачный AI заново и подтвердите лимит расходов.')
            profile_id=cloud_settings.save(body)
            if changed and cloud_settings.vault.exists(profile_id):cloud_settings.vault.delete(profile_id)
            return dict(id=profile_id,key_present=cloud_settings.vault.exists(profile_id),disabled=changed,notice=notice,
                        warning=('Свой адрес: ключ будет отправлен на сервер, который вы указали. '
                                 'Убедитесь, что это ваш сервер или ему можно доверять.') if custom else None)
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

    # n02: чтение кампании и её лент говорит одним языком отказов. Раньше лента
    # старой кампании отдавала 200 и пустоту, а ненайденная — строку с кавычками;
    # игрок видел «Не удалось обработать запрос» и не знал, что делать.
    MISSING_CAMPAIGN=dict(code='campaign_not_found',
        message='Кампания не найдена: возможно, её удалили. Откройте список кампаний и выберите другую.')
    def readable_campaign(campaign):
        """Кампания для чтения: нет такой — 404, сохранение прежней версии — 409."""
        try:current=store.campaign(campaign)
        except KeyError:raise HTTPException(404,MISSING_CAMPAIGN) from None
        from .command_backend import is_native,legacy_refusal
        if not is_native(current):raise HTTPException(409,legacy_refusal())
        return current

    @app.post('/api/campaigns')
    async def create(body:NewCampaign):
        # r210: кампания рождается только через Nat20. Прежний путь считал лист
        # героя рукописным калькулятором (RulesetRepository/RulesetEngine) и
        # принимал кампанию без единого признака движка — так и появлялся
        # rules_backend=legacy. Теперь без сцены Nat20 — отказ с причиной.
        from .command_backend import creation_refusal
        if body.scene is None:raise HTTPException(409,creation_refusal())
        name=body.name.strip()
        if not name:raise ValueError('Введите название кампании.')
        from .playable_ui import create_native_campaign
        async with game.operation:
            result=await asyncio.to_thread(create_native_campaign,store,name,body.scene)
        return dict(id=result['id'],name=result['name'],backend='nat20')
    @app.delete('/api/campaigns/{campaign}')
    async def delete_campaign(campaign:str):
        """Удаление кампании вместе с историей — только по явному подтверждению игрока.

        Возвращаем число удалённых записей — по нему видно, что чистилась вся
        история, а не одна строка кампании. Ключи кабинета, профили Мастера и
        книги сюда не входят: кампания владеет только своей историей.
        """
        async with game.operation:
            try:removed=store.delete_campaign(campaign)
            except KeyError:raise HTTPException(404,MISSING_CAMPAIGN) from None
        return dict(deleted=campaign,rows=removed)

    @app.get('/api/campaigns/{campaign}')
    def state(campaign:str):
        # r210: сохранение прежней версии не открывается. Не миграция и не
        # бейдж совместимости: ручного калькулятора больше нет, поэтому
        # человеку нужна причина и предложение начать новую кампанию.
        current = readable_campaign(campaign)
        current['master']={**Master().model_dump(),**current['master']}
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
        return dict(**public_campaign(current),conversation_cursor=store.conversation_cursor(conversation),
                    conversation_total=total,conversation_more=more,
                    history=history,pending=store.pending(campaign),saves=store.saves(campaign),gm_history=store.gm_history(campaign),conversation=conversation)

    @app.get('/api/campaigns/{campaign}/conversation')
    def conversation_page(campaign:str,before:str='',limit:int=50):
        """Older dialogue, addressed by the cursor the campaign response returned."""
        # Лента — продолжение той же кампании: те же 404 и 409, что у самой
        # кампании, иначе игрок получал пустую историю вместо причины.
        readable_campaign(campaign)
        cursor=None
        if before:
            try:
                created,ident=json.loads(before)
                cursor=(float(created),str(ident))
            except (ValueError,TypeError):
                # Битый курсор приходит только из испорченной ссылки: код и
                # русская причина вместо голого 400 без объяснения.
                raise HTTPException(400,dict(code='history_cursor_invalid',
                    message='Ссылка на продолжение истории повреждена. Откройте кампанию заново — история начнётся с последних реплик.')) from None
        limit=max(1,min(int(limit),200))
        rows=store.conversation_history(campaign,limit=limit,before=cursor)
        return dict(conversation=rows,conversation_cursor=store.conversation_cursor(rows),
                    conversation_more=len(rows)==limit)

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
    def profiles():
        # n02: чтение библиотеки профилей не падает из-за одной записи. Профиль,
        # сохранённый прежней версией или импортированным JSON, может держать
        # значение вне допустимых (`literary.rhythm`='' и подобное); строгий
        # `model_validate` ронял ВЕСЬ список английским дампом pydantic, и экран
        # мастера не открывался вовсе. Теперь запись отдаётся как есть, а `repair`
        # по-русски называет поле, которое надо выбрать заново.
        rows=[]
        for p in store.profiles():
            try:master=Master.model_validate(p['master']).model_dump()
            except ValueError as error:
                reasons=[]
                for detail in list(getattr(error,'errors',lambda:[])())[:3]:
                    loc='.'.join(['master',*[str(part) for part in detail.get('loc',())]])
                    named=_FIELD_TITLES.get(loc) or _FIELD_TITLES.get(loc.split('.')[0]) or loc
                    reasons.append(named+' — '+_VALIDATION_TEXTS.get(detail.get('type'),detail.get('msg') or 'значение не принято'))
                rows.append({**p,'master':p['master'],'repair':'; '.join(reasons)})
                continue
            rows.append({**p,'master':master})
        return rows

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
    # L02: сверка уровня героя с книгой на старте кампании — чтение, «играем
    # так» и пересборка героя. Своё состояние (balance_override) переживает
    # перезапуск, потому что живёт в кампании, а не в экране.
    from .level_gate import register_level_gate_api
    register_level_gate_api(app,store,game,preparation=getattr(app.state,'preparation',None))
    # L02: кампании, начатые до этой правки, не имеют записанного требования
    # книги — сверка на старте молчала бы именно в них. Требование
    # восстанавливается из того же черновика книги одним журнальным событием
    # (без модели и без хода); нет черновика — кампания остаётся как была.
    try:
        from .level_gate import backfill_requirements
        restored=backfill_requirements(store,getattr(app.state,'preparation',None))
        if restored:logger.info('L02: требование книги восстановлено у %s кампаний',len(restored))
    except Exception as exc:logger.warning('L02: восстановление требования книги не удалось: %s',exc)
    # A02: старая кампания открывается всегда — чтение, проверка базы,
    # обновление на копии профиля и выгрузка истории. Своих чисел экран не считает.
    from .nat20_compat import register_nat20_compat_api
    register_nat20_compat_api(app,store,game)
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
