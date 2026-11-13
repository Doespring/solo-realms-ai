"""An item's ALTERNATIVES are picked by the host, not guessed by the engine.

The Foundry packs express a magic item's choice as several sibling effects on
one document -- Armor of Resistance ships a dozen "(Acid)"/"(Cold)"/... effects,
a War Mage wand ships "+1", "+2" and "+3" -- and mark every sibling active,
leaving the choice to the table. Without a recorded pick the engine takes the
first sibling, which is right for an unconfigured item and wrong for one the
player already chose.

``CharacterBuildSpec.item_variants`` records that pick per item slug.
"""

from __future__ import annotations

import pytest

from dnd5e_engine.build_party import project_item_effects
from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_engine.lib_loader import get_lib_loader

ABILITIES = {
    "strength": 14,
    "dexterity": 14,
    "constitution": 14,
    "intelligence": 10,
    "wisdom": 10,
    "charisma": 8,
}

RESISTANCE_ARMOUR = "armor-of-resistance"
WAND = "1-wand-of-the-war-mage"


def _spec(slug, *, variant=None, attuned=True, class_slug="fighter"):
    return CharacterBuildSpec(
        class_slug=class_slug,
        species_slug="human",
        level=5,
        ability_scores=ABILITIES,
        equipment=(slug,),
        attuned_items=(slug,) if attuned else (),
        item_variants={slug: variant} if variant else {},
    )


def _resistances(slug, variant=None):
    return sorted(derive_sheet(_spec(slug, variant=variant), loader=get_lib_loader()).damage_resistances)


def _wand_bonus(variant=None):
    # The wand is attunable "by a Spellcaster", so the wielder must be one.
    effects = project_item_effects(
        _spec(WAND, variant=variant, class_slug="wizard"),
        loader=get_lib_loader(),
        entity_id="hero",
    )
    return [c.value for e in effects for c in e.changes]


def test_without_a_recorded_pick_the_first_alternative_applies():
    """The unconfigured case stays what it was: Acid, and the +1 wand."""
    assert _resistances(RESISTANCE_ARMOUR) == ["acid"]
    assert _wand_bonus() == [1, 1]


def test_the_recorded_pick_decides_which_resistance_is_granted():
    assert _resistances(RESISTANCE_ARMOUR, "Cold Resistance") == ["cold"]
    assert _resistances(RESISTANCE_ARMOUR, "Fire Resistance") == ["fire"]


def test_the_recorded_pick_decides_the_wand_bonus():
    """The bonus is the SIBLING's own value, not a constant."""
    assert _wand_bonus("War Mage +2") == [2, 2]
    assert _wand_bonus("War Mage +3") == [3, 3]


def test_a_pick_the_item_does_not_offer_is_refused():
    """Better a loud error than silently granting a different variant."""
    # The armour really does offer Thunder, so the refusal must be tested with a
    # name it does NOT offer -- otherwise the test proves nothing.
    assert _resistances(RESISTANCE_ARMOUR, "Thunder Resistance") == ["thunder"]
    with pytest.raises(ValueError, match="not one of its alternatives"):
        _resistances(RESISTANCE_ARMOUR, "Flumph Resistance")


def test_a_pick_grants_nothing_until_the_item_is_attuned():
    """Attunement gates the item; the variant only chooses among its effects."""
    assert _resistances(RESISTANCE_ARMOUR, "Cold Resistance") == ["cold"]
    spec = _spec(RESISTANCE_ARMOUR, variant="Cold Resistance", attuned=False)
    assert list(derive_sheet(spec, loader=get_lib_loader()).damage_resistances) == []


def test_the_sheet_and_the_projection_agree_on_the_same_pick():
    """Two seams, one rule: they must not pick different siblings."""
    spec = _spec(WAND, variant="War Mage +3", class_slug="wizard")
    sheet = derive_sheet(spec, loader=get_lib_loader())
    projected = _wand_bonus("War Mage +3")
    assert projected == [3, 3]
    assert sheet is not None
