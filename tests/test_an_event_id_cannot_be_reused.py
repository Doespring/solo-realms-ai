"""An event id already used in this world cannot be imported again."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package_reusing_event():
    return ScenarioPackage(name='Pkg', source='author', locations=[
        dict(name='Hall', description='A new hall.')],
        nodes=[dict(id='n-hall', name='Hall', coordinates=[1, 1])],
        events=[dict(id='e1', kind='notice', text='The bell rings.')])


def _state(tmp_path):
    store = Store(tmp_path / 'evid.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard.', npc=[])],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    state['world']['events'].append(dict(id='e1', kind='notice', text='Old news.'))
    return state


def test_an_event_id_cannot_be_reused(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _package_reusing_event())
    assert 'Идентификатор события уже используется' in str(caught.value), caught.value