"""HTTP/UI seam for the existing command backend; no mechanics or new command DTO."""
import asyncio
import re
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from .contracts import Strict
from .game_commands import GameCommand
from .command_backend import command_backend
from .command_engine import StateVersionError


class NativeCampaign(Strict):
    name: str = Field(min_length=1, max_length=80)
    scene: Literal['lantern-post'] | None = None


def native_character(current):
    """Read the sheet already derived by Nat20, never the legacy sheet calculator."""
    from .nat20_api import Nat20GameEngine
    runtime = Nat20GameEngine._bound(current)
    actors=Nat20GameEngine.visible(Nat20GameEngine.snapshot(current))['actors']
    identity=runtime['hero']
    from .prototype_ui import describe
    from .character_catalog import BASE_CLASSES as RU_CLASSES
    from .character_catalog import FEATURES as RU_FEATURES
    capabilities=Nat20GameEngine.capabilities(current)

    def feature(slug):
        # Nat20's catalogue is English only; the screen is Russian, so a known
        # feature keeps its Russian name and only an unknown one falls back to the
        # technical slug. Nat20 keys are dashed, the catalogue's are underscored.
        row=RU_FEATURES.get(slug.replace('-','_'))
        return dict(describe('features',slug),name=row['name']) if row else describe('features',slug)
    return dict(backend='nat20', version=current['version'], sheet=runtime['sheet'],
                hero=next(actor for actor in actors if actor['id']=='hero'),
                identity=dict(species=describe('species',identity['species_slug'])['name'],
                              classes={(RU_CLASSES.get(key) or describe('classes',key))['name']:level
                                       for key,level in identity.get('classes',{}).items()},
                              level=identity.get('character_level')),
                equipment=[describe('items',key) for key in capabilities['equipment']],
                features=[feature(key) for key in capabilities['features']],
                spells=[describe('spells',key) for key in capabilities['prepared_spells']],
                goals=current['state']['hero'].get('goals',[]))


def register_playable_ui_api(app, store, game):
    @app.get('/api/nat20/options')
    async def options():
        try:
            from .nat20_content import release
            await asyncio.to_thread(release)
        except (ImportError, ValueError, OSError) as error:
            return dict(available=False, message='Тестовый профиль Nat20 недоступен: '+str(error))
        return dict(available=True, character='Человек · воин · уровень 1',
                    equipment='Длинный меч, кольчуга и щит',
                    description='Готовый поддержанный герой. Лист рассчитывает сервер Nat20. '
                                'Сцена «Письмо у старого фонаря»: разговор, следы, один бой, письмо и отдых. Без магии и повышения уровня.')

    @app.post('/api/nat20/campaigns')
    async def create_native(body: NativeCampaign):
        from .nat20_api import Nat20GameEngine
        name=body.name.strip()
        if not name:raise ValueError('Введите имя героя и название испытания.')
        async with game.operation:
            def create():
                result=Nat20GameEngine(store).create_campaign(name, **({'definition_id':'fighter-standard-array-2024-v1'} if body.scene else {}))
                if body.scene:
                    from .lantern_scene import install
                    store.record_scenario(result['id'], 'lantern-install', 'Письмо у старого фонаря', install)
                return result
            result=await asyncio.to_thread(create)
        return dict(id=result['id'],name=result['name'],backend='nat20')

    @app.post('/api/campaigns/{campaign}/commands')
    async def command(campaign: str, body: GameCommand):
        async with game.operation:
            current=store.campaign(campaign)
            backend=command_backend(store,current)
            labels={'check':'Проверка', 'start_combat':'Начать бой', 'move':'Перемещение',
                    'attack':'Атаковать', 'end_turn':'Закончить ход', 'end_combat':'Завершить бой',
                    'short_rest':'Короткий отдых', 'long_rest':'Длительный отдых',
                    'dash':'Рывок', 'use_feature':'Второе дыхание', 'cast_spell':'Благословение',
                    'drop_concentration':'Прекратить концентрацию','native_action':'Действие героя'}
            try:
                result=await asyncio.to_thread(backend.execute,campaign,body,original=labels[body.action.kind])
            except StateVersionError as error:
                raise HTTPException(409,dict(code='version_conflict',message='Сцена изменилась. Обновите её и выберите действие снова.')) from error
            # The same identifiers as a text turn, so the two routes of the one path
            # answer alike and every action can be traced to its event and memory row.
            # The engine's own event id ("nat20:ev1") is not a journal row: only the
            # Solo journal id addresses events, memory and re-narration. Reporting
            # the engine id made PUT /api/events/{id}/narrative answer 404.
            journal=(result.get('journal') or {})
            event_id=journal.get('id')
            memory_key='event:'+str(event_id) if event_id else None
            if event_id:
                try:
                    with store.connection(False) as db:
                        row=db.execute('SELECT min(record_key) FROM memory_records WHERE event_id=?',(event_id,)).fetchone()
                        if row and row[0]:memory_key=row[0]
                except Exception:
                    pass
            identifiers=dict(request_id=body.request_id, action_id=(journal.get('action') or None), event_id=event_id,
                             memory_key=memory_key)
            return dict(status='resolved',command=body.model_dump(mode='json'),identifiers=identifiers,
                        state_version=body.expected_version+1,
                        events=[e.model_dump(mode='json') for e in result['events']],
                        snapshot=backend.visible(result['snapshot']))

    @app.middleware('http')
    async def one_backend(request, call_next):
        if request.method in ('GET','HEAD','OPTIONS'):
            return await call_next(request)
        parts=request.url.path.strip('/').split('/')
        campaign=None;service=False
        try:
            if len(parts)>=4 and parts[0]=='api' and 'campaigns' in parts[1:-1]:
                position=parts.index('campaigns',1)
                campaign=parts[position+1]
                suffix='/'.join(parts[position+2:])
                # Auxiliary records/settings and checkpoint operations do not resolve actions.
                # ``level-up`` resolves no action: it commits one advancement
                # through the Nat20 journal, exactly like the screen's commands.
                # The Nat20 screen posts its commands to screen/commands
                # (solo/play_screen.py) and resolves them through the same backend
                # as commands. Omitting it here refused every screen command of a
                # Nat20 campaign with 409 command_backend_required.
                service=position==1 and bool(re.fullmatch(r'(command-turn|commands|screen/commands|saves|restore/[^/]+|undo|notes|dice|master|gm|level-up)',suffix))
                # The world layer is not a second mechanics backend: it moves the
                # party between places, offers or declines an encounter and keeps
                # markers, and it never resolves an attack, a check or a rest. A
                # prepared book's places and roads are therefore reachable from a
                # Nat20 campaign, while every mechanical action still goes through
                # the Nat20 screen alone.
                if position==1 and suffix in ('world/command','world/preview','world/import'):
                    service=True
            elif len(parts)>=3 and parts[:2]==['api','actions']:
                campaign=store.action(parts[2])['campaign']
            elif len(parts)>=3 and parts[:2]==['api','events']:
                campaign=store.event(parts[2])['campaign']
                service=len(parts)==4 and parts[3]=='narrative' and request.method=='PUT'
            if campaign and not service and 'nat20' in store.campaign(campaign)['state']:
                return JSONResponse(dict(detail=dict(code='command_backend_required',
                    message='В этой кампании действия выполняются через игровой экран Nat20. Прежний обработчик отключён.')),status_code=409)
        except KeyError:
            pass  # Existing handlers retain their normal not-found response.
        return await call_next(request)
