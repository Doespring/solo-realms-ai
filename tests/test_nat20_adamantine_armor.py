"""Adamantine Armor cancels a Critical Hit, and the fight proves it.

SRD 5.2: "While you're wearing it, any Critical Hit against you becomes a
normal hit." Foundry stores this as an ``ada`` ITEM PROPERTY rather than a
change on an effect -- the effect that carries it is an enchantment template
marked ``disabled``, because Foundry uses it to REWRITE the target armour and
then discards it. Reading the template would see nothing, so the engine reads
the property. These tests pin the mechanical outcome, not the flag.
"""

from __future__ import annotations

import asyncio

import pytest

from dnd5e_engine.build_party import project_item_effects
from dnd5e_engine.build_spec import CharacterBuildSpec
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.orchestrator import (
    PlayerIntent,
    advance_monster_turn,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec

ABILITIES = {
    "strength": 10,
    "dexterity": 14,
    "constitution": 14,
    "intelligence": 10,
    "wisdom": 10,
    "charisma": 8,
}

# A seed where the monster rolls a natural 20 against this hero.
CRIT_SEED = 5


def _projection(armour):
    spec = CharacterBuildSpec(
        class_slug="fighter",
        species_slug="human",
        level=5,
        ability_scores=ABILITIES,
        equipment=(armour,),
    )
    return project_item_effects(spec, loader=get_lib_loader(), entity_id="hero")


def _crit_against(armour):
    """Let the monster swing at a hero in ``armour`` on the crit seed."""
    natural, hits, _ = _crit_against_full(armour)
    return natural, hits


def _crit_against_full(armour, *, override=None):
    """``_crit_against`` plus the raw events; ``override`` replaces projection."""
    effects = _projection(armour) if override is None else override.get(armour, [])
    return asyncio.run(_swing(armour, effects))


async def _swing(armour, effects):
    """The monster's single swing at a hero carrying ``effects``."""
    hero = PartyMemberSpec(
        entity_id="hero",
        name="Defender",
        initiative=1,
        hp_current=500,
        hp_max=500,
        ac=5,
        base_speed=30,
        dexterity=14,
        zone_id="1,1",
        character_level=5,
    )
    foe = EncounterMemberSpec(
        entity_id="foe",
        entity_type="Monster",
        name="Attacker",
        initiative=20,
        hp_current=500,
        hp_max=500,
        ac=10,
        attack_bonus=30,
        damage_dice="2d6",
        damage_type="slashing",
        dexterity=14,
        zone_id="2,1",
        base_speed=30,
    )
    result = await start_combat(
        session_id="s",
        party=[hero],
        encounter=[foe],
        grid_scene=GridScene(width=5, height=5),
        rng_seed=CRIT_SEED,
        active_effects=effects,
    )
    handle = result.handle
    drain_pending_events(handle)
    await advance_monster_turn(handle)
    events = drain_pending_events(handle)
    natural = [e.natural for e in events if getattr(e, "type", "") == "attack_rolled"]
    hits = [(e.amount, e.is_crit) for e in events if "damage" in getattr(e, "type", "")]
    return natural, hits, events


def _events(armour):
    """Every event of the single monster swing against a hero in ``armour``."""
    _, _, events = _crit_against_full(armour)
    return events


def _crit_flags(effects_for_armour):
    """The is_crit flag of every attack, with effects chosen per armour."""
    _, hits, _ = _crit_against_full("adamantine-armor", override=effects_for_armour)
    return [is_crit for _amount, is_crit in hits]


def _armour_on(target_id):
    """The Adamantine rule projected onto ``target_id``."""
    return [
        effect.model_copy(update={"target_id": target_id, "id": "item:ada:" + target_id})
        for effect in _projection("adamantine-armor")
    ]


def test_the_armour_projects_a_critical_hit_immunity():
    """The worn armour carries the rule; a plain shirt does not."""
    keys = [(c.key, c.value) for e in _projection("adamantine-armor") for c in e.changes]
    assert ("critical.hit.immunity", None) not in keys  # never a typo-silent key
    assert ("critical.hit.immune", 1) in keys
    assert _projection("chain-shirt") == []


def test_a_natural_20_still_lands_as_a_critical_hit_without_the_armour():
    """The contrast case: the same roll doubles without Adamantine."""
    natural, hits = _crit_against("chain-shirt")
    assert natural == [20], f"expected the crit seed to roll a 20, got {natural}"
    assert hits and hits[0][1] is True


def test_the_armour_turns_that_critical_hit_into_a_normal_hit():
    """Same seed, same natural 20 -- now it is an ordinary hit."""
    natural, plate = _crit_against("adamantine-armor")
    _, plain = _crit_against("chain-shirt")
    assert natural == [20]
    assert plate and plate[0][1] is False, "the Critical Hit was not downgraded"
    assert plate[0][0] < plain[0][0], "a normal hit must do less than a doubled one"


def test_the_hit_is_still_a_hit():
    """The rule cancels the DOUBLING, not the hit itself.

    Asserting on the damage alone would be weak -- 2d6 never rolls 0, so an
    attack that stopped landing would still show a positive number. The event
    itself must say the blow landed and did NOT crit.
    """
    events = _events("adamantine-armor")
    landed = [e for e in events if getattr(e, "type", "") == "attack_rolled"]
    assert landed and landed[0].is_hit is True, "the attack stopped landing"
    assert landed[0].is_crit is False

def test_doffing_the_armour_removes_the_rule():
    """Cessation: the immunity is a property of the WORN armour.

    Doffing is not "the hero wears a different armour" -- the hero keeps the
    same projection and stops wearing the piece that produced it, so the
    projected set becomes empty. The crit must come back.
    """
    worn = _projection("adamantine-armor")
    assert len(worn) == 1, "sanity: wearing it must project exactly the rule"
    before = _crit_flags({"adamantine-armor": worn})
    after = _crit_flags({"adamantine-armor": []})
    assert before == [False], "worn: the crit must be downgraded"
    assert after == [True], "doffed: the crit must come back"


def test_the_rule_survives_a_serialisation_round_trip():
    """The engine stores effects as JSON; the flag must come back intact."""
    from dnd5e_engine.types.effects import ActiveEffect

    effect = _projection("adamantine-armor")[0]
    restored = ActiveEffect.model_validate_json(effect.model_dump_json())
    assert restored == effect
    assert restored.target_id == "hero"
    assert [(c.key, c.mode, c.value) for c in restored.changes] == [
        ("critical.hit.immune", "override", 1)
    ]
