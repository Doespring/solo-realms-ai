"""Importing a book never overwrites a place the party already has."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world import ScenarioPackage, WorldSettings, import_package, observe


def _package(name, description):
    return ScenarioPackage(name='Pkg', source='author', locations=[
        dict(name=name, description=description)],
        nodes=[dict(id='n-new', name=name, coordinates=[1, 1])])


def test_an_import_cannot_rewrite_an_existing_place(tmp_path):
    store = Store(tmp_path / 'import.sqlite3')
    opening = dict(location='Yard', turn=0, facts=[], deferred=[],
                   locations=[dict(name='Yard', description='The yard the party already has.')],
                   world=dict(settings=WorldSettings().model_dump()))
    cid = store.create('Test', Hero(name='Riv'), initial_state=opening)['id']
    state = store.campaign(cid)['state']
    observe(state)
    before = len(state['locations'])
    with pytest.raises(ValueError) as caught:
        import_package(state, _package('Yard', 'Replaced yard'))
    assert 'не перезаписывает' in str(caught.value), caught.value
    assert len(state['locations']) == before