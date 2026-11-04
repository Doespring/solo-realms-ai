"""Exploration must not walk a road toward a place that is already known."""
import os
os.environ['SOLO_TEST']='1'

import json
import pytest

from solo.contracts import Hero
from solo.storage import Store
from solo.world_service import WorldCommand, WorldService


def test_the_known_road_is_not_offered_for_exploration(tmp_path):
    store = Store(tmp_path / 'explore.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    service = WorldService(store)
    service.execute(cid, WorldCommand(request_id='mode', kind='settings',
                                     confirm=True,
                                     settings=dict(mode='sandbox', map_travel=True)))
    state = store.campaign(cid)['state']
    state['locations'] = [dict(state['locations'][0], name=state['location']),
                          dict(state['locations'][0], name='Забытая часовня',
                               known_to_hero=True)]
    state['geography'] = {'nodes': [dict(id='a', name=state['location'],
                                          coordinates=[0, 0]),
                                   dict(id='b', name='Забытая часовня',
                                        coordinates=[6, 0])],
                         'routes': [dict({'from': 'a'}, to='b', meters=100,
                                        minutes=2, description='тропа',
                                        known=True, explorable=True,
                                        modes=['sandbox', 'mixed'])]}
    with store.connection(True) as db:
        db.execute('UPDATE campaigns SET state=? WHERE id=?',
                   (json.dumps(state, ensure_ascii=False), cid))
    with pytest.raises(ValueError) as caught:
        service.execute(cid, WorldCommand(request_id='ex-1', kind='explore',
                                         exit='0'))
    assert 'неисследованной' in str(caught.value), caught.value