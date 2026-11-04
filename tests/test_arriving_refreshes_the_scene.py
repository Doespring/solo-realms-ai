"""Arriving shows the new place, not the one just left."""
import os
os.environ['SOLO_TEST']='1'

from solo import travel
from solo.contracts import Hero
from solo.storage import Store
from solo.world import ensure


def test_arriving_refreshes_the_scene(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    graph = travel.geography(state)
    world = ensure(state)
    world['observations'] = {}
    road = {'from': 'tavern', 'to': 'tower', 'meters': 2000, 'minutes': 30,
            'description': 'лесная тропа', 'known': True, 'timing': 'scenario'}
    travel.advance_path(state, [road], graph)
    seen = ensure(state).get('observations') or {}
    assert 'Старая башня' in seen, (
        "the new place was never observed", seen)