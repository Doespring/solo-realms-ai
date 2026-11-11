"""Declining a fight that was never offered must be refused."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world_service import WorldCommand, WorldService


def test_a_missing_offer_cannot_be_declined(tmp_path):
    store = Store(tmp_path / 'world.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    service = WorldService(store)
    service.execute(cid, WorldCommand(request_id='mode', kind='settings',
                                     confirm=True,
                                     settings=dict(mode='mixed', map_travel=True)))

    with pytest.raises(ValueError) as caught:
        service.execute(cid, WorldCommand(request_id='no-offer',
                                         kind='decline_encounter'))
    assert str(caught.value) == 'Нет предложения столкновения', caught.value