"""The Robe of the Archmagi states its Armor Class as a formula, not a number.

Foundry models that with two changes: ac.calc="custom" and
ac.formula="15 + @abilities.dex.mod". The engine used to model neither, so a
wizard wearing the robe got no AC from it at all.
"""

from __future__ import annotations

import pytest

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.rules.character import evaluate_ac_formula


def _robe(dexterity):
    spec = CharacterBuildSpec(
        class_slug="wizard",
        species_slug="human",
        level=5,
        ability_scores={
            "strength": 10,
            "dexterity": dexterity,
            "constitution": 14,
            "intelligence": 16,
            "wisdom": 10,
            "charisma": 8,
        },
        equipment=("robe-of-the-archmagi",),
        attuned_items=("robe-of-the-archmagi",),
    )
    return derive_sheet(spec, loader=get_lib_loader())


def test_the_robe_sets_ac_from_its_formula():
    """SRD 5.2: "you gain a +6 bonus to Armor Class" via 15 + DEX, so 16 DEX -> 18."""
    sheet = _robe(16)
    assert sheet.ac_calc_mode == "custom"
    assert sheet.ac == 18


def test_the_formula_follows_dexterity():
    """A higher Dexterity raises the robe's AC and a lower one lowers it."""
    assert _robe(19).ac == 15 + 4
    assert _robe(8).ac == 15 - 1


def test_a_formula_is_a_sum_of_constants_and_ability_modifiers():
    modifiers = {"strength": 0, "dexterity": 3, "constitution": 0,
                 "intelligence": 0, "wisdom": 0, "charisma": 0}
    assert evaluate_ac_formula("15 + @abilities.dex.mod", modifiers) == 18
    assert evaluate_ac_formula("10", modifiers) == 10
    assert evaluate_ac_formula("@abilities.dex.mod", modifiers) == 3


def test_an_unreadable_formula_raises_instead_of_guessing():
    """A number nobody can derive is worse than a loud failure."""
    modifiers = {"strength": 0, "dexterity": 3, "constitution": 0,
                 "intelligence": 0, "wisdom": 0, "charisma": 0}
    with pytest.raises(ValueError):
        evaluate_ac_formula("15 + @abilities.foo.mod", modifiers)
    with pytest.raises(ValueError):
        evaluate_ac_formula("15 + max(2, 3)", modifiers)