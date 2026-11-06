"""A local world has a size ceiling."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _big_package(count):
    locations = [dict(name=f'Room {i}', description='A room.') for i in range(count)]
    nodes = [dict(id=f'n-{i}', name=f'Room {i}', coordinates=[i % 80, i // 80])
             for i in range(count)]
    return ScenarioPackage(name='Pkg', source='author',
                           locations=locations, nodes=nodes)


def _state(tmp_path):
    store = Store(tmp_path / 'big.sqlite3')
    seats = [dict(name=f'Old {i}', description='Old.', npc=[]) for i in range(190)]
    opening = dict(location='Old 0', turn=0, facts=[], deferred=[],
                   locations=seats,
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    return state


def test_a_world_cannot_grow_past_its_ceiling(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _big_package(20))
    assert 'Превышен лимит локального мира' in str(caught.value), caught.value