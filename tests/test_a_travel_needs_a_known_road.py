"""Travel goes only where the hero already knows the road."""
import os
os.environ['SOLO_TEST']='1'

import json
import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world_service import WorldCommand, WorldService


def _service(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    service = WorldService(store)
    service.execute(cid, WorldCommand(request_id='mode', kind='settings',
                                     confirm=True,
                                     settings=dict(mode='mixed', map_travel=True)))
    state = store.campaign(cid)['state']
    state['locations'].append(dict(state['locations'][0],
                                   name='Забытая часовня',
                                   known_to_hero=False))
    with store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=? WHERE id=?',
                   (json.dumps(state, ensure_ascii=False), cid))
    return cid, service


def test_the_hero_cannot_travel_to_an_unknown_place(tmp_path):
    cid, service = _service(tmp_path)
    with pytest.raises(ValueError) as caught:
        service.execute(cid, WorldCommand(request_id='go-1',
                                         kind='travel',
                                         target='Забытая часовня'))
    assert str(caught.value) == 'Это место неизвестно герою', caught.value