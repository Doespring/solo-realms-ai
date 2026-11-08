"""A road with an impossible cost stops the journey instead of being walked."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_a_broken_road_is_refused(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    state['geography'] = {'nodes': [n for n in travel.NODES],
                          'routes': [{'from': 'tavern', 'to': 'tower',
                                      'meters': -5, 'minutes': -3,
                                      'description': 'битая дорога',
                                      'known': True}]}
    with pytest.raises(ValueError) as caught:
        travel.itinerary(state, 'Старая башня')
    assert 'повреждён' in str(caught.value), caught.value