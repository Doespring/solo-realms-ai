"""A place that is on the list but not on the map gets a spoken refusal."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_a_place_off_the_map_is_refused_by_name(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    state['locations'].append(dict(state['locations'][0],
                                   name='Соседний холм', known_to_hero=True))
    with pytest.raises(ValueError) as caught:
        travel.advance(state, 'Соседний холм')
    assert 'не знаю дороги' in str(caught.value), caught.value