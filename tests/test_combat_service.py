import asyncio,copy,json
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app,PlayRequest
from solo.storage import Store
from solo.contracts import Hero,Plan
from solo.combat import CombatEngine,Combatant
from solo.combat_view import refresh_vision
from solo.combat_service import CombatService,StartEncounter,CombatCommand,safe_operations,summarize
from solo.combat_api import text_command,plan_command
from solo.geometry import Position
from solo.game import Game
from test_rulesets import Dice
from test_combat import state

def setup(tmp_path):
    store=Store(tmp_path/'combat.sqlite3');cid=store.create('Бой',Hero(name='Герой',weapon_proficiencies=['martial']))['id']
    rng=Dice(20,1,20,8,6)
    service=CombatService(store,rng=rng)
    service.start(cid,StartEncounter(request_id='start',confirm=True,hero_weapon='longsword'))
    return store,cid,service,rng

def test_atomic_replay_stale_checkpoint_undo_restart(tmp_path):
    store,cid,service,rng=setup(tmp_path);before=copy.deepcopy(store.campaign(cid)['state'])
    checkpoint=store.save(cid,'Начало')
    body=CombatCommand(request_id='move',expected_version=1,kind='move',position=Position(x=7,y=4))
    first=service.execute(cid,body)
    assert service.execute(cid,body)['id']==first['id'] and rng.calls==2
    with pytest.raises(ValueError):service.execute(cid,body.model_copy(update={'kind':'dodge'}))
    with pytest.raises(ValueError,match='изменилась'):service.execute(cid,CombatCommand(request_id='stale',kind='attack',target='foe-1',expected_version=1))
    assert store.campaign(cid)['version']==2
    attack=service.execute(cid,CombatCommand(request_id='attack',kind='attack',target='foe-1',expected_version=2))
    assert service.execute(cid,CombatCommand(request_id='attack',kind='attack',target='foe-1',expected_version=2))['id']==attack['id'] and rng.calls==5
    restored=Store(store.path).campaign(cid)
    assert restored['state']['combat']['actors'][1]['stats']['hp']==0
    store.undo(cid);assert store.campaign(cid)['state']['combat']['actors'][1]['stats']['hp']==10
    store.restore(cid,checkpoint);assert store.campaign(cid)['state']==before
    with pytest.raises(ValueError,match='abandoned'):service.execute(cid,body)

def test_text_and_map_commands_same_state_without_interpreter(tmp_path):
    store,cid,service,rng=setup(tmp_path)
    command=text_command('Иду на клетку 8, 5',store.campaign(cid)['state'],'move')
    assert command.position==Position(x=7,y=4)
    class NoAI:
        metrics=[]
        async def interpret(self,*args):raise AssertionError('Exact commands do not call AI')
    game=Game(store,NoAI())
    result=asyncio.run(game.play(cid,PlayRequest(request_id='move',text='Иду на клетку 8, 5')))
    assert result['kind']=='event'
    moved=copy.deepcopy(store.campaign(cid)['state'])
    assert asyncio.run(game.play(cid,PlayRequest(request_id='move',text='Иду на клетку 8, 5')))['event']['id']==result['event']['id']
    store.undo(cid)
    service.execute(cid,command.model_copy(update={'request_id':'map'}))
    assert store.campaign(cid)['state']==moved

def test_negation_conditions_and_compound_do_not_execute():
    for steps in [[dict(kind='attack',text='Не атакую',target='Противник',disposition='negated')],[dict(kind='attack',text='Если подойдёт',target='Противник',condition='Подойдёт')],[dict(kind='move',text='Иду'),dict(kind='attack',text='Бью')]]:
        with pytest.raises(ValueError):plan_command(Plan(summary='Намерение',steps=steps),state(),'intent')
    assert text_command('Не атакую противника',state(),'a') is None
    assert text_command('Атакую противника, если он нападёт',state(),'a') is None

def test_unseen_operations_do_not_reveal_identity_or_weapon():
    s=state();engine=CombatEngine(s);engine.actors['foe'].stats.conditions=['invisible']
    op=dict(kind='attack',actor='foe',target='hero',weapon='club',roll={'secret_bonus':10},damage_dice=[4],hit=False,distance_ft=5,cover_bonus=0)
    text=summarize(engine,[op])
    assert 'Противник' not in text and 'Невидимый' in text
    public=safe_operations(engine,[op])[0]
    assert not set(public)&{'actor','weapon','roll','damage_dice','distance_ft','cover_bonus'}

def test_all_public_state_endpoints_filter_hidden_battle(tmp_path):
    store,cid,service,rng=setup(tmp_path)
    def hide(s):
        s['combat']['actors'][1]['stats']['conditions']=['invisible']
        s['combat']['map']['seed']=987654321
        return [dict(type='scripted',text='Изменён тестовый сценарий')]
    store.record_scenario(cid,'hide','Тест',hide)
    cp=store.save(cid,'Скрытая сцена')
    service.execute(cid,CombatCommand(request_id='look',kind='observe'))
    with TestClient(create_app(store.path)) as client:
        for response in [client.get('/api/campaigns/'+cid),client.get('/api/campaigns/'+cid+'/combat'),client.post('/api/campaigns/'+cid+'/undo'),client.post('/api/campaigns/'+cid+'/restore/'+cp)]:
            assert response.status_code==200,response.text
            payload=response.json();battle=payload.get('state',{}).get('combat',payload)
            assert 'foe-1' not in [a['id'] for a in battle['actors']]
            assert 'seed' not in battle['map']
        event=store.history(cid)[-1]
        packet=client.get('/api/events/'+event['id']+'/prompt').json()['packet']
        assert '987654321' not in json.dumps(packet)

def test_bonus_requires_registered_feature_and_consumes_only_bonus():
    engine=CombatEngine(state());hero=engine.actors['hero']
    with pytest.raises(ValueError):engine.bonus('hero','dash')
    hero.stats.class_name='Плут';hero.stats.level=2;hero.stats.features=['cunning_action']
    engine.bonus('hero','dash')
    assert hero.action and not hero.bonus_action and hero.dash_movement==30
    with pytest.raises(ValueError):engine.bonus('hero','disengage')

def test_ally_never_provokes_friendly_reaction():
    s=state(enemy_position=(8,8,0));engine=CombatEngine(s,rng=Dice())
    ally=Combatant(id='ally',stats=Hero(name='Союзник'),position=Position(x=2,y=1),team='ally')
    engine.actors['ally']=ally
    engine.move('hero',(1,3,0))
    assert ally.reaction
    with pytest.raises(ValueError):engine.attack('hero','ally')

def test_npc_moves_attacks_and_round_consumes_world_time(tmp_path):
    store,cid,service,rng=setup(tmp_path)
    service.rng=Dice(18,4)
    service.execute(cid,CombatCommand(request_id='end',kind='end_turn'))
    event=service.execute(cid,CombatCommand(request_id='npc',kind='npc_turn'))
    battle=store.campaign(cid)['state']
    assert battle['combat']['round']==2 and battle['combat']['cursor']==0
    assert battle['hero']['hp']==6 and battle['elapsed_minutes']==0.1
    assert any(o['kind']=='attack' for o in event['results'][0]['operations'])

def test_hero_reaction_waits_for_choice_without_automatic_attack():
    s=state();s['combat']['cursor']=1
    engine=CombatEngine(s,rng=Dice(20,8,6))
    operations=engine.move('foe',(4,1,0))
    assert operations[-1]['kind']=='reaction_pending'
    assert engine.actors['foe'].position==Position(x=2,y=1) and engine.actors['hero'].reaction
    engine.resolve_reaction('decline')
    assert engine.actors['foe'].position==Position(x=4,y=1) and engine.actors['hero'].reaction

def test_unobserved_obstacle_changes_do_not_inform_move_preview():
    s=state(enemy_position=(8,8,0));engine=CombatEngine(s);refresh_vision(engine);engine.flush()
    baseline=engine.movement_path('hero',(1,3,0))
    engine.geometry.cells[baseline[0][1]].blocked=True
    path,cost=engine.movement_path('hero',(1,3,0))
    assert (path,cost)==baseline and cost==10
    operations=engine._follow_path('hero',path)
    assert operations[0]['kind']=='movement_stop'
