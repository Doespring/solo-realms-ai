"""The hero cannot travel to a place the story never revealed.

solo/travel.py:43 refuses an unknown target. Removing that line left every travel
test green: they move between places the hero already knows. Section 7.1 asks
that movement stay legal, and walking into an unexplored name is exactly the
case a player hits by typing it.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo import travel
from scripts.command_smoke import setup


def test_a_place_the_hero_never_saw_is_refused(tmp_path):
    store, campaign, game = setup(tmp_path)
    state = store.campaign(campaign)['state']
    unknown = 'Место, которого нет'
    assert unknown not in [p['name'] for p in state['locations']]
    with pytest.raises(ValueError) as refused:
        travel.itinerary(state, unknown)
    assert 'неизвестно герою' in str(refused.value), str(refused.value)
