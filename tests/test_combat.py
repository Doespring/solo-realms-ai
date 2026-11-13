import copy
import pytest
from solo.combat import Combatant,CombatEngine
from solo.geometry import Position,rectangular_map
from solo.contracts import Hero
from solo.rulesets import BUILTINS
from test_rulesets import Dice

def state(*,hero_weapon='longsword',enemy_position=(2,1,0),risk=False):
    hero=Combatant(id='hero',stats=Hero(name='Герой',weapon_proficiencies=['martial']),position=Position(x=1,y=1),team='hero',weapon=hero_weapon,ammo=5)
    enemy=Combatant(id='foe',stats=Hero(name='Противник',hp=20,max_hp=20,ac=10),position=Position(x=enemy_position[0],y=enemy_position[1],z=enemy_position[2]),team='enemy',weapon='club')
    return dict(hero=hero.stats.model_dump(),ruleset=BUILTINS['dnd2024'].model_dump(),combat=dict(active=True,map=rectangular_map(10,10).model_dump(),actors=[hero.model_dump(),enemy.model_dump()],order=['hero','foe'],cursor=0,round=1,lethal_risk_declared=risk))

def test_critical_doubles_only_dice_and_damage_resistance():
    s=state();s['combat']['actors'][1]['resistances']=['slashing'];engine=CombatEngine(s,rng=Dice(20,8,6))
    result=engine.attack('hero','foe');engine.flush()
    assert result['critical'] and result['damage_dice']==[8,6]
    assert result['raw_damage']==16 and result['hp_damage']==8
    assert s['combat']['actors'][1]['stats']['hp']==12 and not s['combat']['actors'][0]['action']
    with pytest.raises(ValueError,match='действие'):engine.attack('hero','foe')

def test_illegal_attack_does_not_roll_or_consume_action_ammo():
    s=state(enemy_position=(8,8,0));rng=Dice();engine=CombatEngine(s,rng=rng)
    before=copy.deepcopy(engine.actors['hero'].model_dump())
    with pytest.raises(ValueError,match='дистанции'):engine.attack('hero','foe')
    assert rng.calls==0 and engine.actors['hero'].model_dump()==before

def test_ranged_long_distance_and_occupied_threat_apply_disadvantage():
    s=state(hero_weapon='shortbow');engine=CombatEngine(s,rng=Dice(18,2))
    result=engine.attack('hero','foe');engine.flush()
    assert result['roll']['mode']=='помеха' and not result['hit']
    assert s['combat']['actors'][0]['ammo']==4

def test_massive_damage_outside_announced_risk_preserves_pending_result():
    s=state();engine=CombatEngine(s)
    outcome=engine.damage(engine.actors['hero'],30,'fire');engine.flush()
    assert outcome['mechanical_death'] and outcome['pending_user_decision']
    assert s['hero']['hp']==0 and s['combat']['pending_death']['cause']=='massive_damage'
    assert not s.get('hero_death') and not engine.actors['hero'].dead
    with pytest.raises(ValueError,match='решения'):engine.attack('foe','hero')

def test_announced_risk_allows_recorded_fatal_outcome():
    s=state(risk=True);engine=CombatEngine(s)
    result=engine.damage(engine.actors['hero'],30,'fire');engine.flush()
    assert not result['pending_user_decision'] and s['hero_death']['risk_announced_before'] and engine.actors['hero'].dead

def test_death_saves_keep_dice_and_never_reinvent_success():
    s=state();engine=CombatEngine(s,rng=Dice(1,2));hero=engine.actors['hero'];hero.stats.hp=0
    a=engine.death_save(hero);b=engine.death_save(hero)
    assert a['failures']==2 and b['failures']==3 and b['pending_user_decision']
    assert not hero.dead and engine.data['pending_death']['cause']=='death_saves'

def test_natural_twenty_revives_one_hp_and_nonlethal_leaves_one_hp_unconscious():
    """PHB 2024, печ. стр. 30: «Нокаутирование существа» — 1 Хит и Бессознательность.

    Прежняя редакция этой проверки ждала 0 Хитов и Стабильность: это было
    соглашение прежней редакции, а не правило книги, и движок его повторял.
    """
    s=state();engine=CombatEngine(s,rng=Dice(20));hero=engine.actors['hero'];hero.stats.hp=0;hero.stats.conditions=['unconscious','prone']
    engine.death_save(hero);assert hero.stats.hp==1 and hero.stats.conditions==['prone']
    foe=engine.actors['foe'];engine.damage(foe,22,'bludgeoning',nonlethal=True)
    assert foe.stats.hp==1 and foe.knockout and not foe.stable and not foe.dead
    assert 'unconscious' in foe.stats.conditions

def test_turn_economy_dash_movement_and_reaction_reset():
    s=state();actors=[Combatant.model_validate(a) for a in s['combat']['actors']]
    s.pop('combat')
    engine=CombatEngine.start(s,rectangular_map(10,10),actors,rng=Dice(20,1))
    assert engine.current().id=='hero'
    with pytest.raises(ValueError):engine.movement_path('hero',(8,8,0))
    engine.defend('hero','dash');assert engine.movement_path('hero',(8,8,0))[1]==35
    engine.actors['hero'].reaction=False;engine.end_turn('hero');assert engine.current().id=='foe'
    engine.end_turn('foe');hero=engine.actors['hero']
    assert engine.data['round']==2 and hero.action and hero.reaction and hero.dash_movement==0

def test_2014_surprise_prevents_first_turn_and_reaction_until_end():
    s=state();s['ruleset']=BUILTINS['dnd2014'].model_dump();actors=[Combatant.model_validate(a) for a in s['combat']['actors']];actors[0].surprised=True
    s.pop('combat')
    engine=CombatEngine.start(s,rectangular_map(10,10),actors,rng=Dice(20,1))
    assert not engine.actors['hero'].reaction
    with pytest.raises(ValueError,match='Внезапность'):engine.attack('hero','foe')
    with pytest.raises(ValueError,match='Внезапность'):engine.movement_path('hero',(1,2,0))
    engine.end_turn('hero');assert engine.actors['hero'].reaction

def test_death_saves_use_edition_exhaustion_and_raw_twenty():
    for edition,dice,expected in [('dnd2014',[18,3],3),('dnd2024',[13],7)]:
        s=state();s['ruleset']=BUILTINS[edition].model_dump();engine=CombatEngine(s,rng=Dice(*dice));hero=engine.actors['hero'];hero.stats.hp=0;hero.stats.exhaustion=3
        result=engine.death_save(hero);assert result['total']==expected and result['failures']==1
    s=state();engine=CombatEngine(s,rng=Dice(20));hero=engine.actors['hero'];hero.stats.hp=0;hero.stats.exhaustion=5
    engine.death_save(hero);assert hero.stats.hp==1
