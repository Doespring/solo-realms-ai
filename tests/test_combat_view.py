import copy,json
from solo.combat import CombatEngine
from solo.combat_view import refresh_vision,project,public_campaign
from solo.geometry import rectangular_map,Position
from test_combat import state

def test_unseen_enemy_position_stats_and_map_seed_never_leave_server():
    s=state();s['combat']['map']=rectangular_map(24,20).model_dump();s['combat']['map']['seed']=712345
    s['combat']['actors'][0]['vision_ft']=60
    s['combat']['actors'][1]['position']={'x':23,'y':19,'z':0}
    s['combat']['actors'][1]['stats']['background']='закрытая биография'
    engine=CombatEngine(s);refresh_vision(engine);engine.flush();before=copy.deepcopy(s)
    view=project(s);serialized=json.dumps(view,ensure_ascii=False)
    assert [a['id'] for a in view['actors']]==['hero'] and 'foe' not in serialized
    assert '712345' not in serialized and 'закрытая биография' not in serialized
    assert not any(c['x']==23 and c['y']==19 for c in view['map']['cells'])
    assert s==before and public_campaign({'id':'test','state':s})['state']['combat']==view

def test_last_known_position_never_tracks_current_hidden_motion():
    s=state();s['combat']['map']=rectangular_map(24,20).model_dump()
    s['combat']['actors'][0]['vision_ft']=60
    engine=CombatEngine(s);refresh_vision(engine);engine.actors['foe'].position=Position(x=23,y=19);refresh_vision(engine);engine.flush()
    view=project(s)
    assert [a['id'] for a in view['actors']]==['hero']
    assert view['last_seen'][0]['position']=={'x':2,'y':1,'z':0}
    assert not view['last_seen'][0]['currently_visible']

def test_unobserved_geometry_changes_do_not_update_explored_snapshot():
    s=state();s['combat']['map']=rectangular_map(24,20).model_dump()
    s['combat']['actors'][0]['vision_ft']=60
    engine=CombatEngine(s);refresh_vision(engine)
    engine.actors['hero'].position=Position(x=23,y=19)
    engine.geometry.cells[(1,1,0)].terrain='water';refresh_vision(engine);engine.flush()
    view=project(s);old=next(c for c in view['map']['cells'] if (c['x'],c['y'],c['z'])==(1,1,0))
    assert old['terrain']=='floor' and not old['visible']
