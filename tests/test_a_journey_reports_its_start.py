"""A journey reports the place it started from, not where it ended."""
import os
os.environ['SOLO_TEST']='1'

import json

from solo import travel
from solo.contracts import Hero
from solo.storage import Store


def test_a_journey_reports_its_start_not_its_end(tmp_path):
    store = Store(tmp_path / 'travel.sqlite3')
    cid = store.create('Мир', Hero(name='Мирон'))['id']
    state = store.campaign(cid)['state']
    start = state['location']
    graph = travel.geography(state)
    road = {'from': 'tavern', 'to': 'tower', 'meters': 2000, 'minutes': 30,
            'description': 'лесная тропа', 'known': True, 'timing': 'scenario'}
    result = travel.advance_path(state, [road], graph)
    assert result["from"] == start, ("a journey forgot where it began", result)
    assert result["from"] != result["target"], (
        "the journey started where it ended", result)
    assert result["arrived"] is True