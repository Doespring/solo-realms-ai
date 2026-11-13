"""Attunement restrictions and item charge recovery at rest.

Two gaps from the Stage A-C audit, checked against the real SRD 5.2 corpus:

* P6 -- build_spec._attuned() validated duplicates, the three-item limit and
  equipment membership, but never read Item.attunement_constraint, so a
  Fighter could attune a "by a Wizard" staff. The check now parses that line
  (11 distinct shapes over 21 corpus items, including the "by a Spellcaster"
  class-independent clause and the Dwarven Thrower's "or a Creature Attuned
  to a @UUID[...]" clause whose item label the import lost).
* The charge-recovery seam -- rest.recover_item_uses() existed but was only
  exercised for feature uses. These tests drive it with the corpus's real
  Item.uses.recovery rules: recoverAll (dawn/lr/sr), literal formula ("1"),
  and dice formulas ("1d6 + 1", "4d6 + 2") under a seeded rng.
"""

import random
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dnd5e_engine.build_spec import (  # noqa: E402
    CharacterBuildSpec,
    _species_bases,
    derive_sheet,
    parse_attunement_constraint,
)
from dnd5e_engine.rest import recover_item_uses  # noqa: E402
from dnd5e_srd_data.loader import BundledAssetLoader  # noqa: E402

LOADER = BundledAssetLoader()

SCORES = dict(
    strength=16, dexterity=14, constitution=14, intelligence=12, wisdom=12, charisma=12
)


def sheet(*, classes, equipment, attuned, species="human", level=5):
    spec = CharacterBuildSpec(
        species_slug=species,
        classes=dict(classes),
        level=level,
        ability_scores=SCORES,
        equipment=tuple(equipment),
        attuned_items=tuple(attuned),
    )
    return derive_sheet(spec, loader=LOADER)


# ---------------------------------------------------------------------------
# P6 -- "Requires Attunement by ..." is read, not ignored.
# ---------------------------------------------------------------------------


def test_wand_of_fireballs_needs_a_spellcaster():
    """SRD 5.2 "by a Spellcaster" is class-independent: any casting class counts."""
    assert sheet(classes={"wizard": 5}, equipment=("wand-of-fireballs",),
                 attuned=("wand-of-fireballs",))
    with pytest.raises(ValueError, match="wand-of-fireballs") as err:
        sheet(classes={"fighter": 5}, equipment=("wand-of-fireballs",),
              attuned=("wand-of-fireballs",))
    message = str(err.value)
    assert "by a Spellcaster" in message
    assert "casts no spells" in message


@pytest.mark.parametrize("casting_class", ["paladin", "ranger", "warlock", "bard"])
def test_every_casting_progression_counts_as_spellcaster(casting_class):
    """Half-casters and Pact Magic casters cast spells too (SRD 5.2 Spellcasting)."""
    assert sheet(classes={casting_class: 5}, equipment=("wand-of-fireballs",),
                 attuned=("wand-of-fireballs",))


def test_single_class_restriction():
    """Holy Avenger: "by a Paladin"."""
    assert sheet(classes={"paladin": 5}, equipment=("holy-avenger",),
                 attuned=("holy-avenger",))
    with pytest.raises(ValueError, match="by a Paladin"):
        sheet(classes={"fighter": 5}, equipment=("holy-avenger",),
              attuned=("holy-avenger",))


def test_single_class_druid_restriction():
    """Staff of the Woodlands: "by a Druid"."""
    assert sheet(classes={"druid": 5}, equipment=("staff-of-the-woodlands",),
                 attuned=("staff-of-the-woodlands",))
    with pytest.raises(ValueError, match="by a Druid"):
        sheet(classes={"cleric": 5}, equipment=("staff-of-the-woodlands",),
              attuned=("staff-of-the-woodlands",))


def test_class_list_is_an_or():
    """Necklace of Prayer Beads: "by a Cleric, Druid, or Paladin"."""
    for ok in ("cleric", "druid", "paladin"):
        assert sheet(classes={ok: 5}, equipment=("necklace-of-prayer-beads",),
                     attuned=("necklace-of-prayer-beads",))
    with pytest.raises(ValueError, match="by a Cleric, Druid, or Paladin"):
        sheet(classes={"wizard": 5}, equipment=("necklace-of-prayer-beads",),
              attuned=("necklace-of-prayer-beads",))


def test_multiclass_counts_every_class():
    """A Warlock/Paladin multiclass is a Spellcaster and may use prayer beads."""
    # Paladin multiclassing demands 13 Strength and 13 Charisma (PHB 2024), so a
    # lawful build needs more Charisma than this file's shared street fighter.
    paladin = dict(SCORES, charisma=13)
    spec = CharacterBuildSpec(
        species_slug="human",
        classes={"warlock": 3, "paladin": 2},
        level=5,
        ability_scores=paladin,
        equipment=("necklace-of-prayer-beads",),
        attuned_items=("necklace-of-prayer-beads",),
    )
    assert derive_sheet(spec, loader=LOADER)


def test_dwarven_thrower_species_clause():
    """Dwarven Thrower: "by a Dwarf or a Creature Attuned to a Belt of Dwarvenkind"."""
    assert sheet(species="dwarf", classes={"fighter": 5},
                 equipment=("dwarven-thrower",), attuned=("dwarven-thrower",))
    with pytest.raises(ValueError, match="by a Dwarf") as err:
        sheet(species="human", classes={"fighter": 5},
              equipment=("dwarven-thrower",), attuned=("dwarven-thrower",))
    # Honest about the lost @UUID: the item clause cannot match any name.
    assert "@UUID" in str(err.value)


def test_dwarven_thrower_item_clause_when_the_label_survives():
    """The parser would honour "Belt of Dwarvenkind" if the corpus kept it."""
    restriction = parse_attunement_constraint(
        "dwarven-thrower", "by a Dwarf or a Creature Attuned to a Belt of Dwarvenkind"
    )
    assert restriction.species == ("dwarf",)
    assert restriction.item_names == ("Belt of Dwarvenkind",)
    assert restriction.unparsed is None


def test_lineage_leaf_answers_to_its_base_species():
    """A 2024 leaf ("Elf, High") is still an Elf for a species clause."""
    assert "elf" in _species_bases("elf-high", LOADER)
    assert "dwarf" in _species_bases("dwarf", LOADER)


def test_every_corpus_constraint_parses():
    """All 21 restricted corpus items parse into a modelled clause."""
    restricted = 0
    for slug in LOADER.list_slugs("items"):
        item = LOADER.get_item(slug)
        raw = getattr(item, "attunement_constraint", None)
        if not raw:
            continue
        restricted += 1
        restriction = parse_attunement_constraint(slug, str(raw))
        assert restriction.unparsed is None, (slug, raw, restriction)
        assert (restriction.class_slugs or restriction.spellcaster
                or restriction.species or restriction.item_names), (slug, raw)
    assert restricted >= 21


def test_unparsed_clause_blocks_rather_than_passes():
    """An unknown restriction must refuse, never silently allow."""
    restriction = parse_attunement_constraint("x", "by a Creature of Evil Alignment")
    assert restriction.unparsed
    assert not (restriction.class_slugs or restriction.species or restriction.spellcaster)


def test_a_line_that_does_not_start_with_by_a_is_a_bug():
    with pytest.raises(ValueError, match="not a"):
        parse_attunement_constraint("x", "requires attunement")


def test_unrestricted_items_still_attune():
    """Regression: "Requires Attunement" with no "by a ..." line is free."""
    assert sheet(classes={"fighter": 5}, equipment=("cloak-of-protection",),
                 attuned=("cloak-of-protection",))


def test_attuning_does_not_need_the_item_to_be_equipped_before_any_slug():
    """Regression: the pre-existing rejects still fire."""
    with pytest.raises(ValueError, match="not in equipment"):
        sheet(classes={"wizard": 5}, equipment=("dagger",),
              attuned=("wand-of-fireballs",))


# ---------------------------------------------------------------------------
# Charge recovery at rest -- rest.recover_item_uses on real corpus rules.
# ---------------------------------------------------------------------------


def rules(slug):
    item = LOADER.get_item(slug)
    assert item is not None and item.uses is not None
    return {slug: item.uses.recovery}


def counters(slug, spent):
    return {f"item_use:{slug}": {"spent": spent}}


def test_dawn_recover_all_wind_fan():
    """Wind Fan: recoverAll at dawn."""
    out = recover_item_uses(counters("wind-fan", 3), "dawn", rules("wind-fan"))
    assert out == {"wind-fan": 0}


def test_dawn_recover_all_does_not_fire_at_another_period():
    """A dawn-only pool does not recharge on a Short Rest."""
    out = recover_item_uses(counters("wind-fan", 3), "sr", rules("wind-fan"))
    assert out == {"wind-fan": 3}


def test_lr_recover_all_boots_of_speed():
    out = recover_item_uses(counters("boots-of-speed", 600), "lr", rules("boots-of-speed"))
    assert out == {"boots-of-speed": 0}


def test_sr_recover_all_quarterstaff_of_the_acrobat():
    out = recover_item_uses(
        counters("quarterstaff-of-the-acrobat", 1), "sr",
        rules("quarterstaff-of-the-acrobat"),
    )
    assert out == {"quarterstaff-of-the-acrobat": 0}


def test_literal_formula_regains_one_per_day():
    """Brass Horn of Valhalla: formula "1" at day."""
    out = recover_item_uses(counters("brass-horn-of-valhalla", 5), "day",
                            rules("brass-horn-of-valhalla"))
    assert out == {"brass-horn-of-valhalla": 4}


def test_literal_formula_floors_at_zero():
    out = recover_item_uses(counters("brass-horn-of-valhalla", 0), "day",
                            rules("brass-horn-of-valhalla"))
    assert out == {"brass-horn-of-valhalla": 0}


def test_dice_formula_rolls_only_with_an_rng():
    """Wand of Fireballs: "1d6 + 1" at dawn; no rng means no guessing."""
    kept = recover_item_uses(counters("wand-of-fireballs", 5), "dawn",
                             rules("wand-of-fireballs"))
    assert kept == {"wand-of-fireballs": 5}


def test_dice_formula_is_deterministic_for_a_fixed_seed():
    """The recharge draws exactly one d6 from a seeded stream."""
    expected_regain = random.Random(7).randint(1, 6) + 1
    out = recover_item_uses(counters("wand-of-fireballs", 5), "dawn",
                            rules("wand-of-fireballs"), rng=random.Random(7))
    assert out == {"wand-of-fireballs": max(0, 5 - expected_regain)}


def test_dice_formula_stays_inside_its_range():
    for seed in range(25):
        out = recover_item_uses(counters("wand-of-fireballs", 7), "dawn",
                                rules("wand-of-fireballs"), rng=random.Random(seed))
        # 1d6+1 regains 2..7, so spent lands in 0..5 depending on the draw.
        assert 0 <= out["wand-of-fireballs"] <= 5, (seed, out)


def test_large_dice_formula_staff_of_the_magi():
    """Staff of the Magi: "4d6 + 2" at dawn."""
    for seed in range(10):
        out = recover_item_uses(counters("staff-of-the-magi", 50), "dawn",
                                rules("staff-of-the-magi"), rng=random.Random(seed))
        assert 0 <= out["staff-of-the-magi"] <= 50
    # 4d6+2 always regains at least 6, so 5 spent charges are all regained.
    out = recover_item_uses(counters("staff-of-the-magi", 5), "dawn",
                            rules("staff-of-the-magi"), rng=random.Random(0))
    assert out == {"staff-of-the-magi": 0}


def test_dusk_period_robe_of_stars():
    """Robe of Stars is the corpus's only dusk rule: it does not recharge at dawn."""
    at_dusk = recover_item_uses(counters("robe-of-stars", 4), "dusk",
                                rules("robe-of-stars"), rng=random.Random(3))
    assert 0 <= at_dusk["robe-of-stars"] <= 4
    at_dawn = recover_item_uses(counters("robe-of-stars", 4), "dawn",
                                rules("robe-of-stars"))
    assert at_dawn == {"robe-of-stars": 4}


def test_item_and_feature_counters_do_not_cross():
    """recover_item_uses tracks only item_use: keys, leaving feature_use: alone."""
    mixed = {"item_use:wind-fan": {"spent": 2}, "feature_use:second-wind": {"spent": 1}}
    out = recover_item_uses(mixed, "dawn", rules("wind-fan"))
    assert out == {"wind-fan": 0}


def test_recovery_is_pure():
    counters_in = counters("wand-of-fireballs", 6)
    snapshot = {key: dict(value) for key, value in counters_in.items()}
    recover_item_uses(counters_in, "dawn", rules("wand-of-fireballs"), rng=random.Random(1))
    assert counters_in == snapshot


def test_item_absent_from_the_recovery_map_is_preserved():
    """A caller threading no rule for this item must not silently refill it."""
    out = recover_item_uses(counters("luck-blade", 2), "dawn", {})
    assert out == {"luck-blade": 2}
