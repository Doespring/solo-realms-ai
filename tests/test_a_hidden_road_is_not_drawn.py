"""A road the hero does not know is not drawn on the map."""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.storage import Store
from solo.world import WorldSettings, observe, project


def _state_with_routes(tmp_path):
    store = Store(tmp_path / 'routes.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[]),
                              dict(name='Marsh', description='The marsh.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    graph = state.setdefault('geography', {'nodes': [], 'routes': []})
    graph['nodes'] = [dict(id='a', name='Yard', coordinates=[0, 0]),
                      dict(id='b', name='Marsh', coordinates=[3, 0])]
    graph['routes'] = [dict(id='r1', **{'from': 'a', 'to': 'b'}, meters=500,
                            minutes=30, description='Open road.', known=True),
                       dict(id='r2', **{'from': 'a', 'to': 'b'}, meters=200,
                            minutes=10, description='Secret path.', known=False)]
    return state


def test_a_hidden_road_is_not_projected(tmp_path):
    state = _state_with_routes(tmp_path)
    shown = project(state)
    texts = [r['description'] for r in shown['routes']]
    assert 'Open road.' in texts
    assert 'Secret path.' not in texts