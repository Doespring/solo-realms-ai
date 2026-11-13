"""HTTP/UI seam for the existing command backend; no mechanics or new command DTO."""
import asyncio
import re
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from .contracts import Strict
from .event_display import decorate
from .game_commands import GameCommand
from .command_backend import command_backend
from .command_engine import StateVersionError


class NativeCampaign(Strict):
    name: str = Field(min_length=1, max_length=80)
    scene: Literal['lantern-post'] | None = None


def create_native_campaign(store, name, scene=None):
    """The only way a campaign is born (r210). Both HTTP entries call this.

    ``scene`` is the Nat20 scene the player chose; without it the ordinary
    Nat20 campaign (no book scene) is created. No legacy ruleset is consulted:
    the hero and the sheet come from the Nat20 worker, not from a manual form.
    """
    from .nat20_api import Nat20GameEngine
    engine = Nat20GameEngine(store)
    result = (engine.create_campaign(name, definition_id='fighter-standard-array-2024-v1')
              if scene else engine.create_campaign(name))
    if scene:
        from .lantern_scene import install
        store.record_scenario(result['id'], 'lantern-install', 'Письмо у старого фонаря', install)
    return result


def native_character(current):
    """Read the sheet already derived by Nat20, never the legacy sheet calculator."""
    from .nat20_api import Nat20GameEngine
    runtime = Nat20GameEngine._bound(current)
    actors=Nat20GameEngine.visible(Nat20GameEngine.snapshot(current))['actors']
    identity=runtime['hero']
    from .prototype_ui import describe
    from .character_catalog import BASE_CLASSES as RU_CLASSES
    from .character_catalog import display_name as ru_name
    from .character_catalog import FEATURES as RU_FEATURES
    capabilities=Nat20GameEngine.capabilities(current)

    def feature(slug):
        # Nat20's catalogue is English only; the screen is Russian, so a known
        # feature keeps its Russian name and only an unknown one falls back to the
        # technical slug. Nat20 keys are dashed, the catalogue's are underscored.
        row=RU_FEATURES.get(slug.replace('-','_'))
        return dict(describe('features',slug),name=row['name']) if row else describe('features',slug)
    # A03/F01: items the eight-step master approved belong to this hero even
    # though the Nat20 build owns its own kit. They are shown beside the sheet,
    # never merged into the build: mechanics stay the engine's, the record of
    # what the player brought stays the player's.
    equipped=[describe('items',key) for key in capabilities['equipment']]
    for key in (current['state'].get('preparation') or {}).get('equipment') or ():
        if any(item.get('id')==key for item in equipped):
            continue
        row=describe('items',key)
        if row.get('name')==key:
            from .inventory import CATALOG
            row['name']=(CATALOG.get(key) or {}).get('name') or key
        row['source']='preparation'
        equipped.append(row)
    return dict(backend='nat20', version=current['version'], sheet=runtime['sheet'],
                hero=next(actor for actor in actors if actor['id']=='hero'),
                # n08: имя вида — по-русски (как в мастере героя, r249);
                # английское «Human» на русском листе читалось как данные движка.
                identity=dict(species=ru_name('species',identity['species_slug'],english=describe('species',identity['species_slug'])['name']),
                              classes={(RU_CLASSES.get(key) or describe('classes',key))['name']:level
                                       for key,level in identity.get('classes',{}).items()},
                              level=identity.get('character_level')),
                equipment=equipped,
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
        name=body.name.strip()
        if not name:raise ValueError('Введите имя героя и название испытания.')
        async with game.operation:
            result=await asyncio.to_thread(create_native_campaign,store,name,body.scene)
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
                    'drop_concentration':'Прекратить концентрацию','native_action':'Действие героя',
                    'scene_action':'Действие сцены'}
            # r194: нативную операцию называем её же именем («Захват»), а не
            # общим «Действие героя»: журнал читает игрок.
            label=labels[body.action.kind]
            if body.action.kind=='native_action':
                from .event_display import OP_NAMES
                label=OP_NAMES.get(str(getattr(body.action,'operation','') or ''),label)
            try:
                result=await asyncio.to_thread(backend.execute,campaign,body,original=label)
            except StateVersionError as error:
                from .telemetry import conflict as record_conflict
                record_conflict(campaign,current['version'],expected=getattr(body,'expected_version',None),route='commands')
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
                        # r184: every event carries display_text/display_key, so the
                        # screen renders what the server says instead of parsing
                        # 'Nat20 confirmed: <kind>' in the browser.
                        events=[decorate(e.model_dump(mode='json')) for e in result['events']],
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
                # ``party`` — состав партии (r214): добавление и выбытие
                # спутника пишут один событие в журнал и считают лист тем же
                # движком, что и ход. Это не второй бэкенд механики.
                # ``settings`` (r217) — правила, которые выбирает игрок: смерть,
                # сложность, награды. Запись сохраняет три значения и пишет одно
                # событие; ни броска, ни проверки эта ручка не считает.
                # ``compat/(migrate|verify)`` (A02) — совместимость старой записи:
                # снимок профиля, перепривязка определения и проверка базы. Ни
                # броска, ни проверки, ни хода эти ручки не разрешают.
                # ``level-gate/(accept|rebuild)`` (L02) — решение игрока на
                # старте: «играем так» и пересборка героя. Оба пишут одно
                # журнальное событие и не разрешают ни броска, ни проверки.
                service=position==1 and bool(re.fullmatch(r'(command-turn|commands|screen/commands|saves|restore/[^/]+|undo|notes|dice|master|gm|level-up|party(/[^/]+)?|settings|level-gate/(accept|rebuild)|compat/(migrate|verify))',suffix))
                # The world layer is not a second mechanics backend: it moves the
                # party between places, offers or declines an encounter and keeps
                # markers, and it never resolves an attack, a check or a rest. A
                # prepared book's places and roads are therefore reachable from a
                # Nat20 campaign, while every mechanical action still goes through
                # the Nat20 screen alone.
                if position==1 and suffix in ('world/command','world/preview','world/import'):
                    service=True
                # r236: «Сохранить сюда» кладёт снимок мира в runtime/worlds и не
                # разрешает ни одного действия — ни броска, ни проверки, ни хода.
                if position==1 and suffix=='world/save':
                    service=True
                # Binding a reviewed book to the campaign is the preparation
                # layer, not a second mechanics backend: attach/continue record
                # one scripted journal event and never resolve an attack, a
                # check or a rest. The routes live under review/campaigns/{id},
                # two segments before ``campaigns``, so position is 3; the
                # suffix is still what is checked, never the whole path.
                if parts[1:position]==['adventure','review'] and suffix in ('attach','continue'):
                    service=True
            elif len(parts)>=3 and parts[:2]==['api','actions']:
                campaign=store.action(parts[2])['campaign']
            elif len(parts)>=3 and parts[:2]==['api','events']:
                campaign=store.event(parts[2])['campaign']
                service=len(parts)==4 and parts[3]=='narrative' and request.method=='PUT'
            if campaign:
                current=store.campaign(campaign)
                # r210: сохранение прежней версии не пишет ни одного хода и не
                # принимает ни одной записи — ни игры, ни сохранений, ни
                # служебных записей. Режима совместимости нет, поэтому отказ
                # один и тот же: человеку нужна причина и что делать, а не
                # половина работающего старого пути.
                if 'nat20' not in current['state']:
                    from .command_backend import legacy_refusal
                    return JSONResponse(dict(detail=legacy_refusal()),status_code=409)
                if not service:
                    return JSONResponse(dict(detail=dict(code='command_backend_required',
                        message='В этой кампании действия выполняются через игровой экран Nat20. Прежний обработчик отключён.')),status_code=409)

        except KeyError:
            pass  # Existing handlers retain their normal not-found response.
        return await call_next(request)
