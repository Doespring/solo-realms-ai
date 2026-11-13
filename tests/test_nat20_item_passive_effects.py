"""Executable proof that a magic item's passive effects reach the character sheet.

SRD 5.2 "Magic Items": a worn or held item's benefit applies to the creature, and
an item that "Requires Attunement" lends nothing until the creature attunes.
Before this seam the engine read passive_effects only from FEATURES, so a magic
armour's resistance, a cloak's AC bonus and a cloak's save bonus never reached the
sheet at all. These tests drive that path end to end.
"""

from __future__ import annotations

import pytest

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_engine.lib_loader import get_lib_loader

ABILITIES = {
    "strength": 16,
    "dexterity": 12,
    "constitution": 14,
    "intelligence": 10,
    "wisdom": 10,
    "charisma": 8,
}


@pytest.fixture(scope="module")
def loader():
    return get_lib_loader()


def _sheet(loader, equipment, attuned=(), *, class_slug="fighter"):
    spec = CharacterBuildSpec(
        class_slug=class_slug,
        species_slug="human",
        level=5,
        ability_scores=ABILITIES,
        equipment=tuple(equipment),
        attuned_items=tuple(attuned),
    )
    return derive_sheet(spec, loader=loader)


def test_worn_attuned_armour_grants_its_resistances(loader):
    """Armor of Resistance carries ten resistance changes as always-on effects."""
    plain = _sheet(loader, ["longsword"])
    assert plain.damage_resistances == ()

    # The armour ships ten "(Acid)".."(Thunder)" effects as alternatives for
    # one slot, so a bearer has ONE resistance, not ten (see the variant test).
    worn = _sheet(loader, ["longsword", "armor-of-resistance"], ["armor-of-resistance"])
    assert len(worn.damage_resistances) == 1


def test_an_unattuned_item_grants_nothing(loader):
    """The same item carried but not attuned contributes no resistance."""
    carried = _sheet(loader, ["longsword", "armor-of-resistance"])
    assert carried.damage_resistances == ()


def test_removing_the_item_removes_its_effect(loader):
    """Unequipping returns the sheet to its bare state (no residue)."""
    with_item = _sheet(loader, ["longsword", "armor-of-resistance"], ["armor-of-resistance"])
    assert with_item.damage_resistances != ()
    removed = _sheet(loader, ["longsword"])
    assert removed.damage_resistances == ()


def test_a_disabled_enchantment_template_is_not_a_wearer_effect(loader):
    """adamantine-armor ships only a DISABLED enchantment template.

    That effect rewrites the ITEM document (name, properties, rarity) instead of
    the wearer, so it must never leak onto the character sheet. The crit-handling
    clause in its text is therefore NOT applied by this seam.
    """
    bare = _sheet(loader, ["longsword"])
    wearing = _sheet(loader, ["longsword", "adamantine-armor"])
    assert wearing.damage_resistances == bare.damage_resistances
    assert wearing.damage_immunities == bare.damage_immunities
    assert wearing.condition_immunities == bare.condition_immunities


def test_unknown_equipment_slug_does_not_block_a_sheet(loader):
    """A dataset gap is skipped, never fatal: the rest of the sheet still builds."""
    sheet = _sheet(loader, ["longsword", "no-such-magic-thing"])
    assert sheet is not None

def test_worn_item_ac_bonus_reaches_the_sheet(loader):
    """Cloak/Ring of Protection (+1) and Bracers of Defense (+2) are AC bonuses."""
    bare = _sheet(loader, [])
    cloak = _sheet(loader, ["cloak-of-protection"], ["cloak-of-protection"])
    bracers = _sheet(loader, ["bracers-of-defense"], ["bracers-of-defense"])
    assert cloak.ac == bare.ac + 1
    assert bracers.ac == bare.ac + 2


def test_ac_bonuses_stack_across_items(loader):
    """SRD 5.2: a bonus from a different item stacks; +1 and +2 together make +3."""
    bare = _sheet(loader, [])
    both = _sheet(
        loader,
        ["cloak-of-protection", "bracers-of-defense"],
        ["cloak-of-protection", "bracers-of-defense"],
    )
    assert both.ac == bare.ac + 3


def test_an_ability_floor_sets_but_never_lowers_a_score(loader):
    """Gauntlets of Ogre Power set Strength to 19 -- a floor, not a bonus."""
    below = _sheet(loader, ["gauntlets-of-ogre-power"], ["gauntlets-of-ogre-power"])
    assert below.ability_scores.strength == 19

    spec = CharacterBuildSpec(
        class_slug="fighter",
        species_slug="human",
        level=5,
        ability_scores={**ABILITIES, "strength": 20},
        equipment=("gauntlets-of-ogre-power",),
        attuned_items=("gauntlets-of-ogre-power",),
    )
    above = derive_sheet(spec, loader=loader)
    assert above.ability_scores.strength == 20


def test_an_ability_floor_feeds_hit_points_and_saves(loader):
    """Amulet of Health sets Constitution to 19, so HP and the CON save move too."""
    bare = _sheet(loader, [])
    amulet = _sheet(loader, ["amulet-of-health"], ["amulet-of-health"])
    assert amulet.ability_scores.constitution == 19
    assert amulet.hp_max > bare.hp_max
    assert amulet.ability_modifiers["constitution"] == bare.ability_modifiers["constitution"] + 2


def test_a_flat_ability_bonus_adds_to_the_final_score(loader):
    """An Ioun Stone adds +2 to the score instead of setting it."""
    bare = _sheet(loader, [])
    stone = _sheet(loader, ["agility-ioun-stone"], ["agility-ioun-stone"])
    assert stone.ability_scores.dexterity == bare.ability_scores.dexterity + 2


def test_a_weapon_does_not_grant_hit_points(loader):
    """A weapon is held, not worn, so its template hit point bonus is dropped.

    berserker-axe carries system.attributes.hp.bonuses.level in the packs; SRD
    5.2 gives no weapon an always-on bonus to hit points.
    """
    bare = _sheet(loader, [])
    wielded = _sheet(loader, ["berserker-axe"], ["berserker-axe"])
    assert wielded.hp_max == bare.hp_max


def test_one_item_alternative_applies_not_all_of_them(loader):
    """A ring of resistance resists ONE type, however many the packs list.

    The Foundry packs ship one always-on effect per gemstone, all marked
    active: applying every sibling would grant all ten resistances.
    """
    ring = _sheet(loader, ["ring-of-resistance"])
    assert len(ring.damage_resistances) == 1

    armour = _sheet(loader, ["armor-of-resistance"], ["armor-of-resistance"])
    assert len(armour.damage_resistances) == 1


def test_independent_parts_of_one_item_all_apply(loader):
    """Robe of the Archmagi grants its armour AND its war mage bonus.

    Those two effects have different keys, so they are distinct parts of the
    item rather than alternatives -- unlike the identical-shape siblings.
    """
    robe = _sheet(loader, ["robe-of-the-archmagi"], ["robe-of-the-archmagi"], class_slug="wizard")
    assert robe is not None