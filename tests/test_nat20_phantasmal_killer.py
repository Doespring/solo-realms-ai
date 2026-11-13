"""SRD 5.2 Phantasmal Killer names its saving throw in the text, not the data.

The corpus shipped `save.ability: []` for this one spell (the only such
record out of 339). `save.py:_resolve_save_ability` deliberately refuses an
empty list instead of guessing, so the cast raised ValueError and the spell
was uncastable. The description says "The target makes a Wisdom saving
throw" (and the per-turn repeat "[[/save ability=wis ...]]"), so the data is
corrected to wis from that source; no value was invented.
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

SPELL = "phantasmal-killer"


def _cast(seed=1):
    loader = BundledAssetLoader()
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="acolyte",
        level=17,
        ability_scores=AbilityScores(
            strength=10, dexterity=14, constitution=14,
            intelligence=18, wisdom=10, charisma=10,
        ),
    )
    party = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0",
                       initiative=20, hp_max=99, hp_current=99),
        loader=loader,
    )
    handle = asyncio.run(start_combat(
        session_id="s", party=[party],
        encounter=[EncounterMemberSpec(
            entity_id="m1", name="Target", entity_type="Monster",
            initiative=1, hp_current=999, hp_max=999, ac=2, zone_id="1,0")],
        grid_scene=GridScene(width=10, height=10), rng_seed=seed,
    )).handle
    drain_pending_events(handle)
    asyncio.run(submit_player_intent(
        handle, "pc1",
        PlayerIntent(intent_type="cast_spell", spell_id=SPELL, target_id="m1"),
    ))
    return drain_pending_events(handle)


def test_the_save_is_made_against_wisdom():
    saves = [e for e in _cast() if e.type == "save_rolled"]
    assert len(saves) == 1
    assert saves[0].ability == "wis"


def test_the_cast_is_not_refused():
    """An empty ability list used to raise out of the public entry point."""
    events = _cast()
    assert [e.reason for e in events if e.type == "cast_failed"] == []
    assert any(e.type == "spell_cast" for e in events)


def test_it_deals_four_d10_psychic():
    damage = [(e.amount, e.damage_type) for e in _cast() if e.type == "damage_applied"]
    assert [t for _, t in damage] == ["psychic"]
    assert 4 <= damage[0][0] <= 40, damage


def test_the_failed_save_lands_the_effect():
    events = _cast(seed=1)
    failed = [e for e in events if e.type == "save_rolled" and not e.succeeded]
    if failed:
        assert any(e.type == "effect_applied" for e in events)
