"""A map node must name a place the world actually has."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package_with_orphan_node():
    return ScenarioPackage(name='Pkg', source='author', locations=[
        dict(name='Hall', description='A new hall.')],
        nodes=[dict(id='n-hall', name='Hall', coordinates=[1, 1]),
               dict(id='n-ghost', name='Ghost Room', coordinates=[2, 2])])


def _state(tmp_path):
    store = Store(tmp_path / 'node.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard the party has.')],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    return state


def test_a_node_without_a_place_is_refused(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _package_with_orphan_node())
    assert 'Узел не имеет локации' in str(caught.value), caught.value