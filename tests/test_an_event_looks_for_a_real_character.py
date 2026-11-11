"""An event cannot move a character the world does not know."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package_with_ghost_npc():
    return ScenarioPackage(name='Pkg', source='author', locations=[
        dict(name='Hall', description='A new hall.')],
        nodes=[dict(id='n-hall', name='Hall', coordinates=[1, 1])],
        events=[dict(id='e1', kind='npc_move', npc='Призрак', target='Hall')])


def _state(tmp_path):
    store = Store(tmp_path / 'evnpc.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    return state


def test_an_event_cannot_move_an_unknown_character(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _package_with_ghost_npc())
    assert 'Неизвестн' in str(caught.value), caught.value