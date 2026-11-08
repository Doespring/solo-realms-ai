"""The map of a local world has its own ceiling."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package_adding_nodes(count):
    locations = [dict(name=f'Hall {i}', description='A new hall.') for i in range(count)]
    nodes = [dict(id=f'n-{i}', name=f'Hall {i}', coordinates=[i % 80, i // 80])
             for i in range(count)]
    return ScenarioPackage(name='Pkg', source='author',
                           locations=locations, nodes=nodes)


def _state(tmp_path):
    store = Store(tmp_path / 'geo.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    graph = state.setdefault('geography', {'nodes': [], 'routes': []})
    for i in range(195):
        graph['nodes'].append(dict(id=f'old-{i}', name=f'Old {i}',
                                   coordinates=[i % 80, i // 80]))
    return state


def test_the_map_cannot_grow_past_its_ceiling(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _package_adding_nodes(10))
    assert 'Превышен лимит географии' in str(caught.value), caught.value