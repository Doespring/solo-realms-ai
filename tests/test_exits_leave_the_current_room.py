"""Only the room the party stands in offers exits."""
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.storage import Store
from solo.world import WorldSettings, observe, project


def _state(tmp_path):
    store = Store(tmp_path / 'exits.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[]),
                              dict(name='Hall', description='The hall.', npc=[])],
                   world=dict(settings=WorldSettings(mode='sandbox').model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    graph = state.setdefault('geography', {'nodes': [], 'routes': []})
    graph['nodes'] = [dict(id='a', name='Yard', coordinates=[0, 0], known=True),
                      dict(id='b', name='Hall', coordinates=[3, 0], known=False),
                      dict(id='c', name='Cellar', coordinates=[6, 0], known=False)]
    graph['routes'] = [dict(id='r1', **{'from': 'a', 'to': 'b'}, meters=500,
                            minutes=30, description='Open road.', known=True,
                            explorable=True)]
    graph['routes'].append(dict(id='r2', **{'from': 'b', 'to': 'c'}, meters=300,
                                minutes=15, description='Hall path.', known=True,
                                explorable=True))
    return state


def test_exits_only_lead_out_of_the_current_room(tmp_path):
    state = _state(tmp_path)
    seen_from_yard = project(state)['exits']
    assert len(seen_from_yard) == 1, seen_from_yard
    state['location'] = 'Hall'
    from_hall = project(state)['exits']
    for door in from_hall:
        assert door['direction'] == 'восток', door