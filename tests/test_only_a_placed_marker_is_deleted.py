"""Deleting a marker that was never placed must be refused."""
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


def test_only_a_placed_marker_can_be_deleted(tmp_path):
    cid, service = _service(tmp_path)
    with pytest.raises(ValueError) as caught:
        service.execute(cid, WorldCommand(request_id='del-1',
                                         kind='delete_marker',
                                         marker='нет-такой'))
    assert str(caught.value) == 'Метка не найдена', caught.value