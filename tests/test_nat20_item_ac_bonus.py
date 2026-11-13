"""Who owns a worn magic item's AC bonus -- the sheet, or the fight?

A Cloak of Protection, Bracers of Defense and a Ring of Protection carry
``system.attributes.ac.bonus``. The sheet folds it into ``sheet.ac`` for an
ATTUNED item, so the projection must NOT also hand it to the fight: that would
count it twice. These tests pin that division of labour in both directions,
because getting it wrong is silent -- the number is simply wrong by one or two
and nothing raises.
"""

from __future__ import annotations

from dnd5e_engine.build_party import project_item_effects
from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_engine.lib_loader import get_lib_loader

ABILITIES = {
    "strength": 10,
    "dexterity": 14,
    "constitution": 14,
    "intelligence": 10,
    "wisdom": 10,
    "charisma": 8,
}


def _build(items, attuned=()):
    return CharacterBuildSpec(
        class_slug="fighter",
        species_slug="human",
        level=5,
        ability_scores=ABILITIES,
        equipment=tuple(items),
        attuned_items=tuple(attuned),
    )


def _ac(items, attuned=()):
    return derive_sheet(_build(items, attuned), loader=get_lib_loader()).ac


def _changes(items, attuned=()):
    effects = project_item_effects(
        _build(items, attuned), loader=get_lib_loader(), entity_id="hero"
    )
    return {c.key: c.value for e in effects for c in e.changes}


def test_the_sheet_folds_an_attuned_ac_bonus():
    """Premise: attuned Cloak of Protection is +1 AC on the sheet itself."""
    assert _ac(()) == 12
    assert _ac(["cloak-of-protection"], ["cloak-of-protection"]) == 13


def test_the_sheet_ignores_an_unattuned_ac_bonus():
    """Without attunement the cloak lends nothing -- sheet stays put."""
    assert _ac(["cloak-of-protection"]) == 12


def test_the_projection_does_not_hand_the_same_bonus_to_the_fight():
    """The sheet already carries it; projecting it too would double it."""
    assert "ac.bonus" not in _changes(["cloak-of-protection"], ["cloak-of-protection"])
    assert "ac.bonus" not in _changes(["bracers-of-defense"], ["bracers-of-defense"])
    assert "ac.bonus" not in _changes(["ring-of-protection"], ["ring-of-protection"])


def test_bracers_of_defense_fold_fourteen():
    """Base 12 + 2 from Bracers of Defense, on the sheet."""
    assert _ac(["bracers-of-defense"], ["bracers-of-defense"]) == 14


def test_the_save_bonus_still_reaches_the_fight():
    """Unlike AC, the sheet does not own the save bonus, so it is projected."""
    changes = _changes(["cloak-of-protection"], ["cloak-of-protection"])
    assert changes["save.bonus"] == 1