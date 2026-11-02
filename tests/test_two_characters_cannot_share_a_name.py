"""Two characters cannot answer to the same name."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package_with_twin():
    npc = dict(name='Ирина', character='A local woman.')
    return ScenarioPackage(name='Pkg', source='author', locations=[
        dict(name='Hall', description='A new hall.', npc=[npc, dict(npc)])],
        nodes=[dict(id='n-hall', name='Hall', coordinates=[1, 1])])


def _state(tmp_path):
    store = Store(tmp_path / 'twin.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    return state


def test_two_characters_cannot_share_a_name(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _package_with_twin())
    assert 'однозначно определять личность' in str(caught.value), caught.value