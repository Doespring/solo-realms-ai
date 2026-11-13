"""Требование 8: решения Мастера — явные входы, а не скрытые допущения."""
import dataclasses

import pytest

from dnd5e_engine.activities.context import ActivityResolutionContext

GM_INPUTS = (
    "target_cover",
    "target_unseen",
    "attacker_unseen_by",
    "target_dodging",
    "sneak_attack_ally_adjacent",
    "legendary_resistance_armed",
    "undead_fortitude_holds",
    "pack_tactics_ally_adjacent",
    "target_help_advantage",
)


def test_every_gm_circumstance_is_an_explicit_input():
    names = {f.name for f in dataclasses.fields(ActivityResolutionContext)}
    missing = [n for n in GM_INPUTS if n not in names]
    assert not missing, f"нет входа: {missing}"


def test_the_default_is_empty_not_a_guess():
    """Умолчание обстоятельств — пустая карта, а не выдуманное значение.

    «никто не укрыт» и «никого не видно» — это отсутствие обстоятельства,
    а не решение за Мастера. Явно заданное обстоятельство появляется только
    из входа, поэтому умолчание обязано быть пустым контейнером.
    """
    for name in ("target_cover", "target_unseen", "target_dodging"):
        field = next(f for f in dataclasses.fields(ActivityResolutionContext) if f.name == name)
        factory = field.default_factory
        assert field.default is dataclasses.MISSING, f"{name} имеет скалярное умолчание"
        assert factory is dict, f"{name} умолчание {factory} — не пустая карта"
        assert factory() == {}


def test_cover_is_a_graded_input_not_a_flag():
    from dnd5e_engine.spatial import cover_bonus

    assert cover_bonus("half") == 2
    assert cover_bonus("three_quarters") == 5


def test_an_impossible_action_is_explained():
    from dnd5e_engine.build_spec import derive_sheet, make_build_spec
    from dnd5e_srd_data.loader import BundledAssetLoader

    spec = make_build_spec(
        class_slug="wizard",
        species_slug="human",
        background_slug="acolyte",
        ability_scores={"str": 8, "dex": 14, "con": 12, "int": 8, "wis": 10, "cha": 8},
    )
    multi = spec.model_copy(update={'classes': {'wizard': 1, 'cleric': 1}})
    with pytest.raises(Exception) as excinfo:
        derive_sheet(multi, loader=BundledAssetLoader())
    assert str(excinfo.value), "отказ без объяснения"

