"""Adamantine Armor also stops a Cleave follow-up from critting.

Found by the critic (report of 23.10.2026): the primary attack path was guarded
against ``critical.hit.immune``, but ``_resolve_cleave_chain`` is a SECOND
attack against a different target and resolved its own crit without the guard.
A Greataxe (mastery: cleave) therefore carried a Critical Hit through the
armour -- and so would an auto-crit against an Incapacitated creature in reach,
which needs no natural 20 at all.

These tests drive the real fight: one hero swings at one monster, Cleave
follows through into a second monster standing beside it. The d20 is pinned to
20 through the live RNG object so both attacks crit deterministically -- a seed
search would only prove it for one seed, and a seed where the Cleave misses
proves nothing about the crit.
"""

from __future__ import annotations

import asyncio

from dnd5e_engine.build_party import project_item_effects
from dnd5e_engine.build_spec import CharacterBuildSpec
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.orchestrator import (
    PlayerIntent,
    _get_live,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec

ABILITIES = {
    "strength": 16,
    "dexterity": 14,
    "constitution": 14,
    "intelligence": 10,
    "wisdom": 10,
    "charisma": 8,
}


class _AlwaysTwenty:
    """A d20 that always shows 20; every other draw passes through.

    Pinning only the (1, 20) draw keeps damage dice and initiative real, so the
    fight stays a fight -- only the attack roll is decided.
    """

    def __init__(self, inner):
        self._inner = inner

    def randint(self, a, b):
        if (a, b) == (1, 20):
            return 20
        return self._inner.randint(a, b)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _armour_on(target_id):
    """The Adamantine rule projected onto ``target_id``."""
    spec = CharacterBuildSpec(
        class_slug="fighter",
        species_slug="human",
        level=5,
        ability_scores=ABILITIES,
        equipment=("adamantine-armor",),
    )
    return [
        effect.model_copy(update={"target_id": target_id, "id": "item:ada:" + target_id})
        for effect in project_item_effects(spec, loader=get_lib_loader(), entity_id=target_id)
    ]


async def _cleave_attack(armour_on: str):
    """One Greataxe swing that Cleaves into a second target, both rolling 20."""
    hero = PartyMemberSpec(
        entity_id="hero",
        name="Cleaver",
        initiative=10,
        hp_current=500,
        hp_max=500,
        ac=20,
        base_speed=30,
        strength=16,
        dexterity=14,
        zone_id="1,1",
        character_level=5,
        equipment=["greataxe"],
    )
    primary = EncounterMemberSpec(
        entity_id="primary",
        entity_type="Monster",
        name="First",
        initiative=5,
        hp_current=200,
        hp_max=200,
        ac=5,
        dexterity=8,
        zone_id="2,1",
        base_speed=30,
    )
    candidate = EncounterMemberSpec(
        entity_id="cand",
        entity_type="Monster",
        name="Second",
        initiative=4,
        hp_current=200,
        hp_max=200,
        ac=5,
        dexterity=8,
        zone_id="2,2",
        base_speed=30,
    )
    result = await start_combat(
        session_id="s",
        party=[hero],
        encounter=[primary, candidate],
        grid_scene=GridScene(width=6, height=6),
        rng_seed=5,
        active_effects=_armour_on(candidate.entity_id) if armour_on else [],
    )
    handle = result.handle if hasattr(result, "handle") else result
    _get_live(handle).rng = _AlwaysTwenty(_get_live(handle).rng)
    drain_pending_events(handle)
    await submit_player_intent(
        handle,
        "hero",
        PlayerIntent(intent_type="attack", target_id="primary", weapon_id="greataxe"),
    )
    return drain_pending_events(handle)


def _attacks(armour_on):
    events = asyncio.run(_cleave_attack(armour_on))
    return {e.target_id: e for e in events if getattr(e, "type", "") == "attack_rolled"}


def test_the_cleave_reaches_the_second_target_without_armour():
    """Sanity: this fight really does Cleave, and really does crit twice."""
    attacks = _attacks("")
    assert set(attacks) == {"primary", "cand"}, f"no Cleave in this fight: {attacks}"
    assert attacks["primary"].natural == 20
    assert attacks["primary"].is_crit is True
    assert attacks["cand"].is_crit is True


def test_a_cleave_follow_up_does_not_crit_through_the_armour():
    """The follow-up attack needs the same guard as the primary one."""
    attacks = _attacks("cand")
    assert attacks["primary"].is_crit is True, "the armour must not affect the first target"
    assert attacks["cand"].natural == 20, "sanity: the follow-up rolled a natural 20"
    assert attacks["cand"].is_crit is False, "Cleave carried the Critical Hit through"


def test_the_cleave_still_lands():
    """Again: the doubling goes, the hit stays."""
    attacks = _attacks("cand")
    assert attacks["cand"].is_hit is True


def test_the_armour_does_not_weaken_the_cleaver():
    """An immune target must not change what the attacker's own crit does."""
    plain = _attacks("")
    armoured = _attacks("cand")
    assert plain["primary"].roll_total == armoured["primary"].roll_total
    assert plain["primary"].is_crit == armoured["primary"].is_crit
