"""A place the hero never learned is not a destination."""
import os
os.environ['SOLO_TEST']='1'

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_an_unknown_place_is_not_chosen_by_name(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    state['locations'].append(dict(state['locations'][0],
                                   name='Тихая заводь',
                                   known_to_hero=False))
    assert travel.destination('Тихая заводь', state) is None, (
        'the hero was sent to a place never learned')