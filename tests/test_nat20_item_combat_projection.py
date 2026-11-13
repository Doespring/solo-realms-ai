"""Proof that a worn item's combat-only effects reach the fight.

The sheet carries AC, ability scores, resistances, senses and speed. It cannot
carry a magic weapon's to-hit bonus or a Cloak of Protection's saving-throw
bonus -- those belong to the d20 roll, not the sheet. project_item_effects
translates them into ordinary ActiveEffect documents the host seeds into
start_combat, so the fight reads them through the path it already has.
"""

from __future__ import annotations

from dnd5e_engine.build_party import project_item_effects
from dnd5e_engine.build_spec import CharacterBuildSpec
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.types.effects import ActiveEffect

ABILITIES = {
    "strength": 16,
    "dexterity": 14,
    "constitution": 14,
    "intelligence": 10,
    "wisdom": 10,
    "charisma": 8,
}


def _effects(equipment, attuned=(), *, class_slug="fighter"):
    spec = CharacterBuildSpec(
        class_slug=class_slug,
        species_slug="human",
        level=5,
        ability_scores=ABILITIES,
        equipment=tuple(equipment),
        attuned_items=tuple(attuned),
    )
    return project_item_effects(spec, loader=get_lib_loader(), entity_id="pc1")


def test_a_cloak_of_protection_lends_a_saving_throw_bonus():
    """SRD 5.2: "you gain a +1 bonus to Armor Class and saving throws"."""
    effects = _effects(["cloak-of-protection"], ["cloak-of-protection"])
    keys = [(c.key, c.value) for e in effects for c in e.changes]
    assert ("save.bonus", 1) in keys


def test_the_projected_effect_is_an_ordinary_active_effect():
    """The fight already seeds ActiveEffects; this must not be a new shape."""
    effects = _effects(["cloak-of-protection"], ["cloak-of-protection"])
    assert effects
    for effect in effects:
        assert isinstance(effect, ActiveEffect)
        assert effect.target_id == "pc1"
        assert effect.transfer is True
        assert effect.origin.startswith("item:cloak-of-protection:")


def test_an_unattuned_item_projects_nothing():
    """An attunement-gated item lends nothing until the build attunes."""
    assert _effects(["cloak-of-protection"]) == []


def test_a_casting_focus_lends_its_spell_attack_bonus():
    """Staff of Power: "+2 bonus to attack rolls made with it"."""
    effects = _effects(["staff-of-power"], ["staff-of-power"], class_slug="wizard")
    values = {c.value for e in effects for c in e.changes if c.key == "attack.roll.bonus"}
    assert values == {2}


def test_melee_and_ranged_weapon_keys_fold_into_one_surface():
    """The four Foundry attack buckets all land on attack.roll.bonus."""
    effects = _effects(["robe-of-the-archmagi"], ["robe-of-the-archmagi"], class_slug="wizard")
    keys = {c.key for e in effects for c in e.changes}
    assert "attack.roll.bonus" in keys
    assert not any("bonuses" in key for key in keys)


def test_one_alternative_lends_not_all_of_them():
    """The war mage wands ship +1, +2 and +3 as alternatives for one slot."""
    effects = _effects(["1-wand-of-the-war-mage"], ["1-wand-of-the-war-mage"])
    values = {c.value for e in effects for c in e.changes if c.key == "attack.roll.bonus"}
    assert values == {1}, f"expected only the +1 alternative, got {sorted(values)}"


def test_sheet_owned_effects_are_not_projected_twice():
    """AC and resistances already reach sheet.ac, so they must not ride along."""
    for slug in ("cloak-of-protection", "ring-of-protection", "bracers-of-defense"):
        effects = _effects([slug], [slug])
        keys = {c.key for e in effects for c in e.changes}
        assert not any(k.startswith("ac.") for k in keys), f"{slug} projects AC twice"
        assert not any(k.startswith("system.traits") for k in keys)


def test_a_slug_listed_twice_projects_once():
    """Two entries name one worn object, exactly as the sheet counts it."""
    once = _effects(["cloak-of-protection"], ["cloak-of-protection"])
    twice = _effects(
        ["cloak-of-protection", "cloak-of-protection"], ["cloak-of-protection"]
    )
    assert len(twice) == len(once)