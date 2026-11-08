"""A place the hero does not know must not be walkable.

solo/travel.py:43 refuses an itinerary to an unknown name. The existing travel
tests walk between known places and start fights, so removing that refusal left
them all green: nothing tried to walk somewhere the hero had not found.
"""
import os
os.environ['SOLO_TEST']='1'

import pytest

from scripts.command_smoke import setup
from solo.travel import itinerary


def test_an_unknown_place_is_refused(tmp_path):
    store, campaign, game = setup(tmp_path)
    state = store.campaign(campaign)['state']
    with pytest.raises(ValueError) as refused:
        itinerary(state, 'Ледяная крепость')
    assert 'неизвестно' in str(refused.value), str(refused.value)


def test_a_place_hidden_from_the_hero_is_refused(tmp_path):
    store, campaign, game = setup(tmp_path)
    state = store.campaign(campaign)['state']
    state['locations'].append(dict(name='Тайник', description='Тайник.',
                                   npc=[], objects=[], facts=[], checks={},
                                   known_to_hero=False))
    with pytest.raises(ValueError) as refused:
        itinerary(state, 'Тайник')
    assert 'неизвестно' in str(refused.value), str(refused.value)
