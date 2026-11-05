"""The map has a ceiling on personal markers."""
import os
os.environ['SOLO_TEST']='1'

import json
import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world_service import WorldCommand, WorldService


def _service(tmp_path, markers=None):
    store = Store(tmp_path / 'world.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    service = WorldService(store)
    service.execute(cid, WorldCommand(request_id='mode', kind='settings',
                                     confirm=True,
                                     settings=dict(mode='mixed', map_travel=True)))
    if markers:
        state = store.campaign(cid)['state']
        state['world']['markers'] = markers
        with store.connection(True) as db:
            db.execute('UPDATE campaigns SET state=? WHERE id=?',
                       (json.dumps(state, ensure_ascii=False), cid))
    return cid, service


def test_the_map_refuses_more_than_two_hundred_markers(tmp_path):
    full = [dict(id=str(i), coordinates=[1, 1], text=str(i))
            for i in range(200)]
    cid, service = _service(tmp_path, full)
    with pytest.raises(ValueError) as caught:
        service.execute(cid, WorldCommand(request_id='m1', kind='marker',
                                         coordinates=[10, 5],
                                         text='Ещё одна'))
    assert 'лимит' in str(caught.value).lower(), caught.value