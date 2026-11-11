"""One place has one point on the map."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package_second_node_for_yard():
    return ScenarioPackage(name='Pkg', source='author', locations=[
        dict(name='Hall', description='A new hall.')],
        nodes=[dict(id='n-hall', name='Hall', coordinates=[1, 1]),
               dict(id='n-yard-two', name='Yard', coordinates=[2, 2])])


def _state(tmp_path):
    store = Store(tmp_path / 'twonodes.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    graph = state.setdefault('geography', {'nodes': [], 'routes': []})
    graph['nodes'].append(dict(id='n-yard', name='Yard', coordinates=[0, 0]))
    return state


def test_a_place_cannot_get_a_second_node(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _package_second_node_for_yard())
    assert 'уже имеет узел карты' in str(caught.value), caught.value