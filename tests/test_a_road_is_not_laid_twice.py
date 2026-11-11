"""The same road between two nodes is not laid twice."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package_relaying_old_road():
    return ScenarioPackage(name='Pkg', source='author', locations=[
        dict(name='Hall', description='A new hall.')],
        nodes=[dict(id='n-hall', name='Hall', coordinates=[1, 1])],
        routes=[dict(from_='old-0', to='n-hall', meters=100, minutes=60, description='A road.')])


def _state(tmp_path):
    store = Store(tmp_path / 'route.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    graph = state.setdefault('geography', {'nodes': [], 'routes': []})
    graph['nodes'].append(dict(id='old-0', name='Old 0', coordinates=[0, 0]))
    graph['routes'].append(dict(**{'from': 'old-0'}, to='n-hall', meters=50, minutes=30))
    return state


def test_a_second_road_between_the_same_nodes_is_refused(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _package_relaying_old_road())
    assert 'уже существует' in str(caught.value), caught.value