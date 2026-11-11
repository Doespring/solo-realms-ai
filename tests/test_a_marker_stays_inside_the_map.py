"""A marker must sit inside the map and say something."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world_service import WorldCommand, WorldService


def _service(tmp_path):
    store = Store(tmp_path / 'world.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    service = WorldService(store)
    service.execute(cid, WorldCommand(request_id='mode', kind='settings',
                                     confirm=True,
                                     settings=dict(mode='mixed', map_travel=True)))
    return cid, service


def test_a_marker_off_the_map_is_refused(tmp_path):
    cid, service = _service(tmp_path)
    with pytest.raises(ValueError) as caught:
        service.execute(cid, WorldCommand(request_id='m1', kind='marker',
                                         coordinates=[120, 5],
                                         text='Клад'))
    assert 'координат' in str(caught.value).lower(), caught.value


def test_a_marker_without_words_is_refused(tmp_path):
    cid, service = _service(tmp_path)
    with pytest.raises(ValueError) as caught:
        service.execute(cid, WorldCommand(request_id='m2', kind='marker',
                                         coordinates=[10, 5], text='   '))
    assert 'координат' in str(caught.value).lower(), caught.value