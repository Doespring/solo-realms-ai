"""A fallen hero cannot walk away from the scene."""
import os
os.environ['SOLO_TEST']='1'

import pytest

from solo.contracts import Hero
from solo.rulesets import BUILTINS
from solo.storage import Store
from solo.world_service import travel_guard


def _state(**hero):
    store = Store.__new__(Store)
    return dict(location="Двор", locations=[dict(name="Двор"), dict(name="Сад")],
                hero=Hero(name="Герой", **hero).model_dump(), turn=1)


def test_a_fallen_hero_cannot_travel():
    state = _state(hp=0)
    with pytest.raises(ValueError) as caught:
        travel_guard(state)
    assert 'не может идти' in str(caught.value).lower(), caught.value


def test_an_unconscious_hero_cannot_travel():
    state = _state(conditions=["unconscious"])
    with pytest.raises(ValueError) as caught:
        travel_guard(state)
    assert 'не может идти' in str(caught.value).lower(), caught.value