"""A room already on the map offers no unexplored exit."""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.storage import Store
from solo.world import WorldSettings, observe, project


def test_a_known_neighbour_is_not_offered_as_an_exit(tmp_path):
    store = Store(tmp_path / 'known.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[]),
                              dict(name='Hall', description='The hall.', npc=[])],
                   world=dict(settings=WorldSettings(mode='sandbox').model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    graph = state.setdefault('geography', {'nodes': [], 'routes': []})
    graph['nodes'] = [dict(id='a', name='Yard', coordinates=[0, 0], known=True),
                      dict(id='b', name='Hall', coordinates=[3, 0], known=True)]
    graph['routes'] = [dict(id='r1', **{'from': 'a', 'to': 'b'}, meters=500,
                            minutes=30, description='Open road.', known=True,
                            explorable=True)]
    shown = project(state)
    assert shown['exits'] == []
    assert len(shown['routes']) == 1