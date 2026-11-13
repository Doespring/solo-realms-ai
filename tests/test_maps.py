import copy,json,time
import pytest
from solo.maps import MapDescription,generate,TEMPLATES,validate
from solo.geometry import Position,MapObject,rectangular_map,Geometry,Cell
from solo.combat import Combatant,CombatEngine
from solo.combat_view import refresh_vision,project
from solo.combat_service import CombatService,StartEncounter,CombatCommand
from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store
from test_rulesets import Dice

def encounter(template='ruins',*,lighting='bright',rng=None):
    field,hero,foe=generate(MapDescription(environment=template,lighting=lighting))
    h=Combatant(id='hero',stats=Hero(name='Герой'),position=hero,team='hero')
    f=Combatant(id='foe',stats=Hero(name='Противник'),position=foe,team='enemy')
    state={'hero':h.stats.model_dump(),'ruleset':BUILTINS['dnd2024'].model_dump()}
    return CombatEngine.start(state,field,[h,f],rng=rng or Dice(20,1))

@pytest.mark.parametrize('template',TEMPLATES)
def test_generation_is_deterministic_connected_and_bounded(template):
    for seed in range(10):
        d=MapDescription(environment=template,seed=seed)
        a,h,f=generate(d);b,h2,f2=generate(d)
        assert a.model_dump()==b.model_dump() and h==h2 and f==f2
        assert len(a.cells)<500 and all(c.x<a.width and c.y<a.height for c in a.cells)
        validate(a,h,f)

def test_door_geometry_reveal_and_saves_undo_are_one_transaction(tmp_path):
    store=Store(tmp_path/'maps.sqlite3');cid=store.create('Руины',Hero(name='Герой'))['id']
    service=CombatService(store,rng=Dice(20,1))
    service.start(cid,StartEncounter(request_id='start',confirm=True,template='ruins'))
    before=project(store.campaign(cid)['state'])
    assert [a['id'] for a in before['actors']]==['hero']
    assert 'secret-door' not in json.dumps(before)
    service.execute(cid,CombatCommand(request_id='move',kind='move',position=Position(x=7,y=6)))
    checkpoint=store.save(cid,'Перед дверью')
    service.execute(cid,CombatCommand(request_id='door',kind='interact',target='main-door'))
    opened=store.campaign(cid)['state'];public=project(opened)
    assert len(public['actors'])==2
    cell=next(c for c in opened['combat']['map']['cells'] if c['x']==8 and c['y']==6)
    assert not cell['blocked'] and not cell['opaque']
    assert Store(store.path).campaign(cid)['state']==opened
    store.undo(cid);assert len(project(store.campaign(cid)['state'])['actors'])==1
    store.restore(cid,checkpoint);assert not next(o for o in store.campaign(cid)['state']['combat']['map']['objects'] if o['id']=='main-door')['open']

def test_light_and_darkvision_change_visibility_without_leaking_light_source():
    engine=encounter('room',lighting='dark')
    public=project(engine.state)
    assert len(public['actors'])==1 and 'foe' not in public['order']
    assert engine.sees(engine.actors['hero'],(1,6,0))
    engine.interact('hero','entry-lamp');refresh_vision(engine);engine.flush()
    assert len([c for c in project(engine.state)['map']['cells'] if c['visible']])==1
    engine.actors['hero'].darkvision_ft=90;refresh_vision(engine);engine.flush()
    assert engine.sees(engine.actors['hero'],engine.actors['foe']) and len(project(engine.state)['actors'])==2

def test_secret_search_failure_does_not_reveal_presence_then_success_discovers():
    engine=encounter();hero=engine.actors['hero'];hero.position=Position(x=7,y=2)
    engine.rng=Dice(1)
    result=engine.search('hero',(8,2,0))
    assert result['found']==[] and 'dc' not in result['roll']
    assert not any(o['id']=='secret-door' for o in project(engine.state)['map']['objects'])
    hero.action=True;engine.rng=Dice(20)
    result=engine.search('hero',(8,2,0));refresh_vision(engine);engine.flush()
    assert result['found']==['secret-door'] and not hero.action
    assert any(o['id']=='secret-door' for o in project(engine.state)['map']['objects'])

def test_unknown_adjacent_step_explores_but_distant_unknown_does_not_reveal():
    engine=encounter('room',lighting='dark');hero=engine.actors['hero']
    engine.interact('hero','entry-lamp');refresh_vision(engine);engine.flush()
    # Discard former observations to emulate a newly discovered dark room.
    engine.data['known_cells']={k:v for k,v in engine.data['known_cells'].items() if k=='1,6,0'}
    with pytest.raises(ValueError,match='соседнюю'):engine.movement_path('hero',(5,6,0))
    path,cost=engine.movement_path('hero',(2,6,0))
    assert cost==5 and path[-1]==(2,6,0)
    engine.move('hero',(2,6,0));engine.flush()
    assert '2,6,0' in engine.data['known_cells']

def test_balcony_stairs_are_real_links_and_fall_uses_active_preset():
    engine=encounter('balcony');hero=engine.actors['hero']
    for pos in [(2,6,0),(3,6,1),(4,6,2),(5,6,3),(6,6,4)]:
        engine.move('hero',pos)
    engine.flush()
    assert hero.position.z==4
    hero.movement_spent=0;engine.rng=Dice(6,5)
    info=engine.fall_preview('hero',(5,5,0))
    assert info['height_ft']==20 and info['dice_count']==2
    result=engine.drop('hero',(5,5,0));engine.flush()
    assert result['dice']==[6,5] and hero.position.z==0 and hero.stats.hp==1 and 'prone' in hero.stats.conditions

def test_ceiling_and_low_obstacle_cover_are_directional():
    field=rectangular_map(10,10);geometry=Geometry(field,BUILTINS['dnd2024'])
    geometry.cells[(3,2,0)].cover=2
    assert geometry.cover((1,2,0),(5,2,0))==2
    assert geometry.cover((1,6,0),(5,6,0))==0
    field.cells.append(Cell(x=5,y=2,z=1,opaque_floor=True))
    geometry=Geometry(field,BUILTINS['dnd2024'])
    assert not geometry.line_of_sight((5,2,0),(5,2,1))

def test_invalid_imported_geometry_rolls_no_dice_or_mutation(tmp_path):
    store=Store(tmp_path/'import.sqlite3');cid=store.create('Мир',Hero(name='Герой'))['id'];rng=Dice();service=CombatService(store,rng=rng)
    field=rectangular_map();before=copy.deepcopy(store.campaign(cid))
    with pytest.raises(ValueError):service.start(cid,StartEncounter(request_id='bad',confirm=True,imported_map=field,hero_position=Position(x=1,y=1),enemy_position=Position(x=1,y=1)))
    assert store.campaign(cid)==before and rng.calls==0

def test_fall_confirm_warning_precedes_unchanged_state(tmp_path):
    engine=encounter('balcony');hero=engine.actors['hero'];hero.position=Position(x=6,y=6,z=4)
    refresh_vision(engine);engine.flush()
    # Ground was observed before climbing; restored here to represent that exploration.
    for c in engine.map.cells:
        if c.key()==(5,5,0):engine.data['known_cells']['5,5,0']=c.model_dump(exclude={'secret'})
    before=copy.deepcopy(engine.state);info=engine.fall_preview('hero',(5,5,0))
    assert 'смертельный' in info['warning'] and engine.state==before
