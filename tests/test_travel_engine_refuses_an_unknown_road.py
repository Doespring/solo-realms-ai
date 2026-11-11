"""The travel engine itself refuses a road to an unknown place."""
import os
os.environ['SOLO_TEST']='1'

import json
import pytest

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_advance_refuses_an_unknown_target_by_itself(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    state['locations'].append(dict(state['locations'][0],
                                   name='Забытая часовня',
                                   known_to_hero=False))
    with pytest.raises(ValueError) as caught:
        travel.advance(state, 'Забытая часовня')
    assert str(caught.value) == 'Это место ещё неизвестно герою', caught.value