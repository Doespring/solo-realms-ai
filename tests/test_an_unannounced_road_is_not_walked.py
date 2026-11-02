"""A road nobody told the hero about is not walked."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_an_unannounced_road_is_not_walked(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    state['geography'] = {'nodes': list(travel.NODES),
                          'routes': [{'from': 'tavern', 'to': 'tower',
                                      'meters': 100, 'minutes': 5,
                                      'description': 'тайная тропа',
                                      'known': False}]}
    with pytest.raises(ValueError) as caught:
        travel.itinerary(state, 'Старая башня')
    assert 'не известна дорога' in str(caught.value), caught.value