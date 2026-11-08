"""A hero laden past their strength cannot walk to the next place."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.world_service import travel_guard

HEAVY = dict(id="anvil", name="Наковальня", owner="hero", quantity=1,
             source="scenario", weight_centilb=100000)


def _state(**hero):
    hero_body = Hero(name="Герой", **hero).model_dump()
    return dict(location="Двор", locations=[dict(name="Двор")],
                hero=hero_body, turn=1,
                economy=dict(items=[HEAVY], wallet_cp=0, merchants=[]))


def test_an_overloaded_hero_cannot_travel():
    state = _state()
    with pytest.raises(ValueError) as caught:
        travel_guard(state)
    assert 'Поклажа' in str(caught.value), caught.value


def test_a_light_hero_may_travel():
    state = _state()
    state['economy']['items'] = []
    assert travel_guard(state) > 0