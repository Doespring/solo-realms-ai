"""End to end: a worn item's bonus survives into the running fight.

project_item_effects is only worth anything if the engine actually reads what
it returns. This opens a real combat through start_combat and asks the live
handle what effects the hero carries -- the same path a session takes.
"""

from __future__ import annotations

import asyncio

from dnd5e_engine.build_party import project_item_effects
from dnd5e_engine.build_spec import CharacterBuildSpec
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.orchestrator import get_actor_active_effects, start_combat
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec

ABILITIES = {
    "strength": 16,
    "dexterity": 14,
    "constitution": 14,
    "intelligence": 10,
    "wisdom": 10,
    "charisma": 8,
}


def _combat_with(attuned):
    """Open a fight for a hero wearing (and maybe attuned to) the cloak."""
    spec = CharacterBuildSpec(
        class_slug="fighter",
        species_slug="human",
        level=5,
        ability_scores=ABILITIES,
        equipment=("cloak-of-protection",),
        attuned_items=tuple(attuned),
    )
    effects = project_item_effects(spec, loader=get_lib_loader(), entity_id="pc1")
    hero = PartyMemberSpec(
        entity_id="pc1",
        name="Tester",
        initiative=12,
        hp_current=44,
        hp_max=44,
        ac=15,
        base_speed=30,
        dexterity=14,
        zone_id="1,1",
        character_level=5,
    )
    foe = EncounterMemberSpec(
        entity_id="foe1",
        entity_type="Monster",
        name="Goblin",
        initiative=5,
        hp_current=7,
        hp_max=7,
        ac=15,
        dexterity=14,
        zone_id="3,3",
        base_speed=30,
    )
    result = asyncio.run(
        start_combat(
            session_id="s1",
            party=[hero],
            encounter=[foe],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=7,
            active_effects=effects,
        )
    )
    return get_actor_active_effects(result.handle, "pc1")


def test_the_cloaks_saving_throw_bonus_reaches_the_running_fight():
    """The hero holds a live effect carrying save.bonus +1."""
    carried = _combat_with(["cloak-of-protection"])
    changes = [(c.key, c.value) for e in carried for c in e.changes]
    assert ("save.bonus", 1) in changes


def test_an_unattuned_cloak_adds_nothing_to_the_fight():
    """Attunement gates the bonus in combat exactly as it does on the sheet.

    A NEGATIVE case is only worth having next to the positive one it denies:
    asserting an empty result passes for a projection that emits nothing at
    all. So the same helper must carry the cloak when attuned — measured in
    this very test — and nothing when it is not.
    """
    carried = _combat_with(["cloak-of-protection"])
    assert any(
        (c.key, c.value) == ("save.bonus", 1)
        for e in carried
        for c in e.changes
    ), f"the attuned cloak must reach the fight, got {carried!r}"
    assert _combat_with([]) == ()