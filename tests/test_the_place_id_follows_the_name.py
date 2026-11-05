"""The place name and its map id move together."""
import os
os.environ['SOLO_TEST']='1'

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_the_place_id_follows_the_place_name(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    graph = travel.geography(state)
    road = {'from': 'tavern', 'to': 'tower', 'meters': 2000, 'minutes': 30,
            'description': 'лесная тропа', 'known': True, 'timing': 'scenario'}
    travel.advance_path(state, [road], graph)
    assert state['location'] == 'Старая башня', state['location']
    assert state['location_id'] == 'tower', (
        "the place id stayed behind at the old spot", state['location_id'])