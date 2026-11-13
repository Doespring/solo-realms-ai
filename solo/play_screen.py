"""Player UI adapter over existing services; no rule resolution in this boundary.

screen-v1 projects engine DTOs for the player and retains legacy campaign services.
Availability probes resolve existing commands on disposable copies, never the journal.
"""
import asyncio
from copy import deepcopy
import json
from typing import Literal
from pydantic import Field
from fastapi import HTTPException
from .contracts import Strict, Hero
from .combat_service import CombatService, CombatCommand, StartEncounter
from .combat_view import public_campaign
from .rulesets import RulesetEngine, active
from .check_text import attack_text
from .event_display import fields


class ScreenCommand(Strict):
    request_id: str = Field(min_length=1, max_length=80)
    expected_version: int = Field(ge=0)
    kind: Literal['combat', 'rest', 'start', 'restore', 'engine']
    parameters: dict = Field(default_factory=dict)


def uses_commands(current):
    state=current['state']
    if 'nat20' in state:return True
    return not state.get('adventure') and not state.get('world',{}).get('events') and bool(state.get('engine_snapshot') or any(p.get('checks') for p in state.get('locations',[])))


def commands_for(current, battle):
    result=[];version=current['version'];service=CombatService(None)
    if uses_commands(current):
        from .command_engine import CommandEngine
        from .game_commands import GameCommand as EngineCommand
        engine=CommandEngine(None)
        candidates=[]
        if battle and battle['active']:
            candidates=[('Атаковать: '+a['name'],dict(kind='attack',target=a['id'])) for a in battle['actors'] if a['team']=='enemy']
            candidates += [('Закончить ход',dict(kind='end_turn')),('Завершить бой',dict(kind='end_combat'))]
        else:
            candidates=[('Начать базовое столкновение',dict(kind='start_combat',encounter_id='basic')),('Короткий отдых',dict(kind='short_rest',hit_dice=0)),('Длительный отдых с пищей и водой',dict(kind='long_rest',food_water=True))]
            place=next((p for p in current['state']['locations'] if p['name']==current['state']['location']),{})
            candidates[:0]=[('Проверить: '+key,dict(kind='check',check_id=key)) for key in place.get('checks',{})]
        for label,action in candidates:
            try:
                probe=deepcopy(current)
                engine.resolve(probe,EngineCommand(request_id='availability',expected_version=version,action=action))
            except ValueError:continue
            result.append(dict(id='engine:'+str(len(result)),label=label,kind='engine',parameters=action,expected_version=version))
        return result
    def offer(label,kind,parameters,probe):
        try:probe(deepcopy(current['state']))
        except ValueError:return
        result.append(dict(id=kind+':'+str(len(result)),label=label,kind=kind,parameters=parameters,expected_version=version))
    if battle and battle['active']:
        candidates=[('Атаковать: '+a['name'],dict(kind='attack',target=a['id'])) for a in battle['actors'] if a['team']=='enemy']
        candidates += [(label,dict(kind=kind)) for kind,label in [('end_turn','Закончить ход'),('npc_turn','Ход противника'),('dodge','Уклонение'),('disengage','Отход'),('dash','Рывок'),('stand_up','Встать'),('finish','Завершить бой')]]
        if battle.get('pending_reaction'):
            candidates=[(label,dict(kind='reaction',choice=choice)) for choice,label in [('attack','Реакция: атаковать'),('decline','Отказаться от реакции')]]
        for label,parameters in candidates:
            command=CombatCommand(request_id='availability',expected_version=version,**parameters)
            offer(label,'combat',parameters,lambda state,command=command:service.execute_transition(current,command,state))
    else:
        from .first_playable import require_full_scene
        def rest(state,kind):
            require_full_scene(state)
            return RulesetEngine(active(state)).rest(state,kind)
        for kind,label in [('short','Короткий отдых'),('long','Длительный отдых')]:
            offer(label,'rest',dict(kind=kind),lambda state,kind=kind:rest(state,kind))
        pending=current['state'].get('world',{}).get('pending_encounter')
        if pending:
            parameters=dict(proposal=pending['id'],confirm=True)
            command=StartEncounter(request_id='availability',expected_version=version,**parameters)
            offer('Подтвердить столкновение','start',parameters,lambda state:service.start_transition(current,command,state))
    return result


def roll_from(results):
    latest=None
    for result in results:
        for effect in result.get('game_event',{}).get('payload',{}).get('effects',[]):
            if effect['type']=='roll':
                value=effect['payload'];latest=dict(label={'check':'Проверка','attack':'Атака','initiative':'Инициатива'}.get(value['kind'],'Бросок'),dice=value['dice'],total=value['total'],success=value.get('success'),formula='',
                    # r226: «нужно · выпало · модификатор = итог» — только числа движка.
                    dc=value.get('dc'),modifier=value.get('modifier'))
        if result.get('type')=='check':
            latest=dict(label='Проверка',dice=result.get('dice',[]),total=result.get('total'),success=result.get('success'),formula=result.get('formula',''),
                dc=result.get('dc'),modifier=result.get('modifier'))
        for op in result.get('operations',[]):
            roll=op.get('roll')
            if isinstance(roll,dict):latest=dict(label='Атака' if op['kind']=='attack' else 'Бросок',dice=roll.get('dice',[]),total=roll.get('total'),success=op.get('hit'),formula=attack_text(op) if op['kind']=='attack' else roll.get('formula',''),
                dc=roll.get('dc'),modifier=roll.get('modifier'))
            elif op.get('kind')=='attack':latest=dict(label='Атака',dice=roll.get('dice',[]) if isinstance(roll,dict) else [],total=roll.get('total') if isinstance(roll,dict) else None,success=op.get('hit'),formula=attack_text(op),
                dc=None,modifier=roll.get('modifier') if isinstance(roll,dict) else None)
            elif op.get('kind')=='death_save':latest=dict(label='Спасбросок от смерти',dice=[op['die']],total=op['die'],success=None,formula='',
                dc=op.get('dc'),modifier=None)
    return latest


def screen(store,campaign,*,nat20_projector=None):
    current=store.campaign(campaign);public=public_campaign(current)['state'];hero=public['hero']
    native=None
    if 'nat20' in current['state']:
        from .nat20_screen import Nat20Screen
        native=(nat20_projector or Nat20Screen()).project(current)
        sheet=None
    else:sheet=RulesetEngine(active(current['state'])).sheet(Hero.model_validate(hero))
    if not native and public.get('combat') and not public['combat']['active']:
        for actor in public['combat']['actors']:
            if actor['id']=='hero':actor.update(hp=hero['hp'],max_hp=sheet['effective_max_hp'],conditions=hero.get('conditions',[]))
    events=[];last_roll=None
    # Read the existing player history projection; never expose raw snapshots or GM data.
    history=store.history(campaign)
    with store.connection() as db:
        raw={row['id']:json.loads(row['results']) for row in db.execute('SELECT id,results FROM events WHERE campaign=? ORDER BY seq DESC LIMIT 50',(campaign,))}
    for event in history:
        if event['undone']:continue
        results=event['results']
        # Raw DTOs remain server-side. Project only events addressed to this hero.
        typed=[r for r in raw.get(event['id'],[]) if r.get('game_event',{}).get('scope') in ('public','private') and 'hero' in r.get('game_event',{}).get('known_by',[])]
        if typed:results=typed
        roll=roll_from(results)
        if roll:last_roll=roll
        text=' '.join(r.get('game_event',{}).get('payload',{}).get('outcome') or r.get('text') or r.get('reply') or '' for r in results) or event['original']
        # r184: the screen renders what the server hands it — one readable sentence
        # and a stable key (solo/event_display.py). Old journal rows stored the
        # engine's own line ('Nat20 confirmed: check'); it is translated here, on
        # the way out, and the browser no longer knows that string at all.
        kind=''
        if native and typed:
            kind=typed[0].get('game_event',{}).get('payload',{}).get('command') or ''
        events.append(dict(id=event['id'],sequence=event['seq'],text=text,roll=roll,**fields(kind,text)))
    feedback=None
    conversations=store.conversation_history(campaign)
    if conversations and (not history or conversations[-1]['created']>history[-1]['created']):
        reply=conversations[-1]
        with store.connection() as db:
            row=db.execute('SELECT context FROM conversations WHERE id=?',(reply['id'],)).fetchone()
        meta=json.loads(row['context']) if row else {}
        if not meta.get('command_cycle') or meta.get('expected_version')==current['version']:
            feedback=dict(kind=meta.get('command_status') or ('clarification' if reply['kind']=='clarification' else 'unsupported' if 'не поддерживается' in reply['reply'] else 'ready'),text=reply['reply'])
    hero_packet=native['hero'] if native else dict(name=hero['name'],hp=hero['hp'],max_hp=sheet['effective_max_hp'],conditions=hero.get('conditions',[]),
            resources=[dict(id=k,label={'second_wind':'Второе дыхание','spell_slots_1':'Ячейки 1 круга'}.get(k,k),**v) for k,v in hero.get('resources',{}).items()],
            hit_dice=sheet['hit_dice_available'])
    if native:
        labels={'experience':'Опыт','concentration':'Концентрация'}
        for resource in hero_packet['resources']:
            if resource['id'].startswith('spell_slot:'):resource['label']='Ячейки '+resource['id'].split(':',1)[1]+' круга'
            elif resource['id'] in labels:resource['label']=labels[resource['id']]
    snapshot=dict(campaign_id=campaign,version=current['version'],command_turn=uses_commands(current),
                  hero=hero_packet,combat=native['combat'] if native else public.get('combat'),last_roll=last_roll)
    if native:
        snapshot['backend']='nat20'
        snapshot['terminal']=native['terminal']
        # A02: вердикт совместимости и три действия едут вместе со сценой, чтобы
        # несовместимость была видна ДО ввода, а не после отказа движка.
        snapshot['compatibility']=native.get('compatibility')
        # L02: сверка уровня героя с книгой — Мастер говорит об этом до первого
        # броска, поэтому числа и выбор едут в том же пакете, что и сцена.
        from . import level_gate
        snapshot['level_gate']=level_gate.projection(current['state'])
        # r217: the player's own rules and the hero's out-of-fight state, so the
        # screen can show what the numbers mean without asking another route.
        snapshot['settings']=native.get('settings')
        snapshot['out']=native.get('out')
        from .lantern_scene import projection
        snapshot['scene']=projection(current['state']) or dict(title=public['location'],
            description=next((place.get('description','') for place in public['locations'] if place['name']==public['location']),''))
    return dict(contract_version='screen-v1',feedback=feedback,snapshot=snapshot,
                events=events[-12:],commands=native['commands'] if native else commands_for(current,public.get('combat')))


def register_play_screen_api(app,store,game):
    native_projector=None
    @app.get('/api/campaigns/{campaign}/screen')
    async def get_screen(campaign:str):
        nonlocal native_projector
        async with game.operation:
            if native_projector is None and 'nat20' in store.campaign(campaign)['state']:
                from .nat20_screen import Nat20Screen
                native_projector=Nat20Screen()
            if 'nat20' in store.campaign(campaign)['state']:
                return await asyncio.to_thread(screen,store,campaign,nat20_projector=native_projector)
            return screen(store,campaign,nat20_projector=native_projector)

    @app.post('/api/campaigns/{campaign}/screen/commands')
    async def execute(campaign:str,body:ScreenCommand):
        async with game.operation:
            current=store.campaign(campaign)
            # Replays must recover the committed event before checking its old version.
            existing=store.request(campaign,body.request_id)
            if (existing is None or body.kind=='restore') and current['version']!=body.expected_version:
                from .telemetry import conflict as record_conflict
                record_conflict(campaign,current['version'],expected=body.expected_version,route='screen/commands')
                raise HTTPException(409,dict(code='version_conflict',message='Сцена изменилась. Обновите её и выберите действие снова.'))
            parameters=body.parameters
            if 'nat20' in current['state'] and body.kind not in ('engine','restore'):
                raise ValueError('Кампания Nat20 принимает только команды общего backend.')
            if body.kind=='engine':
                from .command_backend import command_backend
                from .game_commands import GameCommand as EngineCommand
                label={'check':'Проверка: '+str(parameters.get('check_id','')),'start_combat':'Начать бой','attack':'Атаковать цель','end_turn':'Закончить ход','end_combat':'Завершить бой','short_rest':'Короткий отдых','long_rest':'Длительный отдых с пищей и водой','scene_action':'Действие сцены'}.get(parameters.get('kind'),'Действие героя')
                # r208: вызов движка — подпроцесс на 1,5-2,5 с; в корутине он держит цикл.
                resolved=await asyncio.to_thread(command_backend(store,current).execute,campaign,EngineCommand(request_id=body.request_id,expected_version=body.expected_version,action=parameters),original=label)
                return dict(kind='event',event=game.public_event(resolved['journal']))
            elif body.kind=='combat':
                event=CombatService(store).execute(campaign,CombatCommand(**parameters,request_id=body.request_id,expected_version=body.expected_version))
            elif body.kind=='start':
                event=CombatService(store).start(campaign,StartEncounter(**parameters,request_id=body.request_id,expected_version=body.expected_version))
            elif body.kind=='rest':
                from .rules_api import RestRequest,apply_rest
                rest=RestRequest(**parameters,request_id=body.request_id)
                event=apply_rest(store,campaign,rest.request_id,rest.kind,rest.hit_dice,rest.food_water)
            else:
                class Restore(Strict):checkpoint:str=Field(min_length=1,max_length=80)
                restore=Restore(**parameters);store.backup();store.restore(campaign,restore.checkpoint)
                return dict(kind='restored')
            return dict(kind='event',event=game.public_event(event))
