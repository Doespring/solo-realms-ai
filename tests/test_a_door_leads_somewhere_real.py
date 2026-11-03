"""A door in a book must lead somewhere the book already has."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package_with_door(destination, name='Hall'):
    return ScenarioPackage(name='Pkg', source='author', locations=[
        dict(name=name, description='A new hall.', interactions=[
            dict(id='d1', name='Door', kind='door',
                 destination=destination, accessible=True)])],
        nodes=[dict(id='n-hall', name=name, coordinates=[1, 1])])


def _state(tmp_path):
    store = Store(tmp_path / 'door.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard the party has.')],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    return state


def test_a_door_to_nowhere_is_refused(tmp_path):
    state = _state(tmp_path)
    with pytest.raises(ValueError) as caught:
        import_package(state, _package_with_door('Cellar'))
    assert 'Дверь ведёт в неизвестное' in str(caught.value), caught.value