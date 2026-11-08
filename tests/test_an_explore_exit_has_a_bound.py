"""An exploration exit number must point at a road that was offered."""
import os
os.environ['SOLO_TEST']='1'

import json
import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world_service import WorldCommand, WorldService


def _two_roads(tmp_path):
    store = Store(tmp_path / 'explore.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    service = WorldService(store)
    service.execute(cid, WorldCommand(request_id='mode', kind='settings',
                                     confirm=True,
                                     settings=dict(mode='sandbox', map_travel=True)))
    state = store.campaign(cid)['state']
    for name, point in (('Забытая часовня', [6, 0]), ('Тихая заводь', [3, 3])):
        state['locations'].append(dict(state['locations'][0], name=name,
                                       known_to_hero=False))
    state['geography'] = {
        'nodes': [dict(id='a', name=state['location'], coordinates=[0, 0]),
                  dict(id='b', name='Забытая часовня', coordinates=[6, 0]),
                  dict(id='c', name='Тихая заводь', coordinates=[3, 3])],
        'routes': [dict({'from': 'a'}, to=to, meters=100, minutes=2,
                       description='тропа', known=True, explorable=True,
                       modes=['sandbox', 'mixed'])
                   for to in ('b', 'c')]}
    with store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=? WHERE id=?',
                   (json.dumps(state, ensure_ascii=False), cid))
    return cid, service


def test_a_number_past_the_offered_roads_is_refused(tmp_path):
    cid, service = _two_roads(tmp_path)
    with pytest.raises(ValueError) as caught:
        service.execute(cid, WorldCommand(request_id='ex-1', kind='explore',
                                         exit='5'))
    assert 'неисследованной' in str(caught.value), caught.value