"""A map node id already in use cannot quietly move to another place."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package_reusing_a_node_id():
    return ScenarioPackage(name='Pkg', source='author', locations=[
        dict(name='Hall', description='A new hall.')],
        nodes=[dict(id='n-yard', name='Hall', coordinates=[1, 1])])


def _state(tmp_path):
    store = Store(tmp_path / 'nodeid.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard the party has.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    graph = state.setdefault('geography', {'nodes': [], 'routes': []})
    graph.setdefault('nodes', []).append(
        dict(id='n-yard', name='Yard', coordinates=[0, 0]))
    return state


def test_a_node_id_cannot_move_to_another_place(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _package_reusing_a_node_id())
    assert 'занят другой локацией' in str(caught.value), caught.value