"""Class features that need the caster's spellcasting ability resolve it.

A USE_FEATURE invocation built its activity context without
`spellcasting_ability`, so every feature whose save DC is
`calculation == "spellcasting"` (Cleric Sear Undead, Paladin Abjure Foes)
raised `ValueError: save.dc.calculation == 'spellcasting' requires a caster
spellcasting ability but the context supplies none` out of the public entry
point, and every whole-number formula written with `*` (Monk Slow Fall
"5 x your Monk level", Improved Blessed Strikes "2 x wis mod") raised
`Unsupported binary op in dice expression: '*'`.

A feature is not a spell, so no per-spell Magic Initiate override applies:
the class ability alone is correct. Each test asserts the STATE CHANGE, not
a status word.
"""
import asyncio

from dnd5e_engine import (
    GridScene,
    PlayerIntent,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec
from dnd5e_srd_data.loader import BundledAssetLoader


def _fight(cls, level, *, wisdom=18, hp=100, seed=7):
    loader = BundledAssetLoader()
    spec = CharacterBuildSpec(
        species_slug="human", class_slug=cls, background_slug="acolyte",
        level=level,
        ability_scores=AbilityScores(
            strength=16, dexterity=14, constitution=16,
            intelligence=16, wisdom=wisdom, charisma=16,
        ),
    )
    party = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0",
                       initiative=20, hp_max=200, hp_current=hp),
        loader=loader,
    )
    handle = asyncio.run(start_combat(
        session_id="s", party=[party],
        encounter=[EncounterMemberSpec(
            entity_id="m1", name="Target", entity_type="Monster",
            initiative=1, hp_current=999, hp_max=999, ac=12, zone_id="1,0")],
        grid_scene=GridScene(width=10, height=10), rng_seed=seed,
    )).handle
    drain_pending_events(handle)
    return handle


def _use(handle, slug):
    asyncio.run(submit_player_intent(
        handle, "pc1",
        PlayerIntent(intent_type="use_feature", feature_id=slug, target_id="m1"),
    ))
    return drain_pending_events(handle)


def test_sear_undead_saves_against_the_cleric_save_dc():
    events = _use(_fight("cleric", 6), "sear-undead")
    assert [e.reason for e in events if e.type == "cast_failed"] == []
    saves = [e for e in events if e.type == "save_rolled"]
    assert len(saves) == 1
    # 8 + proficiency 3 (level 6) + WIS +4 = 15.
    assert saves[0].dc == 15
    assert saves[0].ability == "wis"


def test_sear_undead_deals_radiant_damage():
    damage = [(e.amount, e.damage_type) for e in _use(_fight("cleric", 6), "sear-undead")
              if e.type == "damage_applied"]
    assert [t for _, t in damage] == ["radiant"]
    assert damage[0][0] > 0, damage


def test_abjure_foes_saves_and_applies_the_effect():
    events = _use(_fight("paladin", 11), "abjure-foes")
    assert [e.reason for e in events if e.type == "cast_failed"] == []
    saves = [e for e in events if e.type == "save_rolled"]
    assert saves and saves[0].ability == "wis"
    assert any(e.type == "effect_applied" for e in events)


def test_slow_fall_multiplies_the_monk_level():
    """'5 x your Monk level' at Monk 11 is 55 healing, not 0 and not a crash."""
    heals = [e for e in _use(_fight("monk", 11), "slow-fall")
             if e.type == "healing_applied"]
    assert [h.amount for h in heals] == [55]


def test_improved_blessed_strikes_grants_temphp_from_wisdom():
    """'2 x wis mod' with WIS 18 is 8 temporary hit points."""
    events = _use(_fight("cleric", 17), "improved-blessed-strikes")
    granted = [e for e in events if e.type == "temphp_applied"]
    assert len(granted) == 1, [e.type for e in events]
    assert granted[0].amount == 8
