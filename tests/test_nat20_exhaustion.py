"""SRD 5.2 Exhaustion: two scaling penalties, not the 2014 ladder.

2014 used isolated disadvantage/penalty tiers; 2024 replaces them with a flat
numeric effect: -2 x level on every D20 Test and -5 x level feet of Speed.
`rules/conditions.py` carried a comment claiming the level "is tracked but
applies no penalty", which contradicted the code: `d20_test_penalty` and
`project_speed` implement both penalties and the orchestrator calls them at
every roll site. The comment is corrected here, and this file pins the numbers
so the claim is checked rather than trusted.
"""
import pytest

from dnd5e_engine.rules.conditions import d20_test_penalty, project_speed
from dnd5e_engine.types.conditions import ActiveCondition


def _exhausted(level):
    return ActiveCondition(
        condition="exhaustion",
        source_entity_id="implied:exhaustion",
        scope="session",
        duration_rounds=None,
        save_dc=None,
        applied_round=0,
        exhaustion_level=level,
        source_effect_id=None,
    )


@pytest.mark.parametrize("level,penalty", [(0, 0), (1, -2), (2, -4), (3, -6), (4, -8), (5, -10), (6, -12)])
def test_the_d20_penalty_is_two_per_level(level, penalty):
    conditions = [_exhausted(level)] if level else []
    assert d20_test_penalty(conditions) == penalty


@pytest.mark.parametrize("level,speed", [(0, 30), (1, 25), (2, 20), (3, 15), (4, 10), (5, 5), (6, 0)])
def test_speed_drops_five_feet_per_level(level, speed):
    names = ["exhaustion"] if level else []
    assert project_speed(30, names, level) == speed


def test_speed_never_goes_negative():
    assert project_speed(10, ["exhaustion"], 5) == 0
    assert project_speed(3, ["exhaustion"], 6) == 0


def test_the_highest_level_wins_not_the_sum():
    """Two entries carrying levels cannot be added together."""
    assert d20_test_penalty([_exhausted(2), _exhausted(1)]) == -4


def test_a_grappled_creature_has_zero_speed_regardless_of_exhaustion():
    """SRD 5.2 Grappled: Speed is 0 and can't increase."""
    assert project_speed(30, ["grappled", "exhaustion"], 1) == 0


def test_no_exhaustion_means_no_penalty():
    assert d20_test_penalty([]) == 0
    assert project_speed(30, [], 0) == 30

