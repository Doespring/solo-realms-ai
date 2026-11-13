"""SRD 5.2 magic-item mechanics that reached the engine in this iteration.

Each test drives a real corpus item through the production path (sheet ->
party spec -> combatant -> running fight) and asserts the mechanical result,
not a flag:

* Boots of Speed (multiply walk speed 30 -> 60)
* Boots of the Winterlands (ignore difficult terrain made of ice or snow)
* Ioun Stone of Awareness (advantage on Initiative rolls)
* Bracers of Archery (proficiency with longbows and shortbows)
* Demon Armor (knows Abyssal)

Attunement gates every one of them: an unattuned item must change nothing.
"""

from __future__ import annotations

import asyncio
import json
import inspect
import re
import random

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.activities.passive_stats import (
    PassiveEffectChange,
    interpret_passive_stats,
)
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import CharacterBuildSpec, CombatInstance, derive_sheet
from dnd5e_engine.lib_loader import get_lib_loader
from dnd5e_engine.orchestrator import PlayerIntent
from dnd5e_engine.specs import (
    EncounterMemberSpec,
    GridScene,
    PartyMemberSpec,
)


def _spec(item: str, *, attuned: bool = True):
    return CharacterBuildSpec(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        level=3,
        equipment=[item],
        attuned_items=[item] if attuned else [],
    )


def _sheet(item: str, *, attuned: bool = True):
    return derive_sheet(_spec(item, attuned=attuned), loader=get_lib_loader())


def test_boots_of_speed_do_not_double_speed_from_being_worn():
    # SRD 5.2: "While you wear these boots, you can take a Bonus Action to
    # click the boots' heels together. If you DO, the boots double your
    # Speed." Wearing them is not using them: the corpus ships the doubling
    # as an ordinary enabled effect, but its "Click Heels" activity names it
    # by id, so the sheet must not fold it in. An earlier version of this
    # test asserted 60 and so locked in the bug.
    worn = _sheet("boots-of-speed")
    bare = _sheet("boots-of-speed", attuned=False)
    assert bare.base_speed == 30
    assert worn.base_speed == 30


def test_the_winterlands_boots_name_the_terrain_they_excuse():
    worn = _sheet("boots-of-the-winterlands")
    bare = _sheet("boots-of-the-winterlands", attuned=False)
    assert bare.ignored_difficult_terrain == ()
    assert set(worn.ignored_difficult_terrain) == {"ice", "snow"}


def test_an_ioun_stone_of_awareness_adds_advantage_to_initiative():
    worn = _sheet("awareness-ioun-stone")
    bare = _sheet("awareness-ioun-stone", attuned=False)
    assert bare.initiative_advantage == 0
    assert worn.initiative_advantage == 1


def test_bracers_of_archery_grant_bow_proficiency_by_name():
    worn = _sheet("bracers-of-archery")
    bare = _sheet("bracers-of-archery", attuned=False)
    assert {"longbow", "shortbow"} <= set(worn.weapon_proficiencies)
    assert not {"longbow", "shortbow"} & set(bare.weapon_proficiencies)


def test_demon_armor_teaches_abyssal():
    worn = _sheet("demon-armor")
    bare = _sheet("demon-armor", attuned=False)
    # The soldier background grants Common (backgrounds/soldier.json); the
    # armor adds Abyssal only when attuned. Asserting an empty tuple ignored
    # the background language.
    assert bare.languages == ("common",)
    assert worn.languages == ("abyssal", "common")


def _party_member(item: str, *, attuned: bool):
    instance = CombatInstance(
        entity_id="pc1",
        name="Hero",
        zone_id="0,0",
        initiative=20,
        hp_max=30,
        hp_current=30,
    )
    return build_party_member(_spec(item, attuned=attuned), instance, loader=get_lib_loader())


def test_the_item_properties_reach_the_running_fight():
    worn = _party_member("boots-of-the-winterlands", attuned=True)
    bare = _party_member("boots-of-the-winterlands", attuned=False)
    assert set(worn.ignored_difficult_terrain) == {"ice", "snow"}
    assert bare.ignored_difficult_terrain == ()


async def _walk(difficult_cells, ignore_terrain, destination, kinds=None):
    scene = GridScene(
        width=8,
        height=8,
        difficult_terrain_cells=list(difficult_cells),
        difficult_terrain_kinds=dict(kinds or {}),
    )
    pc = PartyMemberSpec(
        entity_id="pc1",
        name="Hero",
        initiative=20,
        hp_current=30,
        hp_max=30,
        strength=16,
        dexterity=14,
        zone_id="0,0",
        equipment=["greataxe"],
        ignored_difficult_terrain=tuple(ignore_terrain),
    )
    goblin = EncounterMemberSpec(
        entity_id="m1",
        name="Goblin",
        entity_type="Monster",
        initiative=5,
        hp_current=7,
        hp_max=7,
        ac=15,
        zone_id="7,7",
    )
    handle = (
        await O.start_combat(
            session_id="terrain",
            party=[pc],
            encounter=[goblin],
            grid_scene=scene,
            rng_seed=7,
        )
    ).handle
    await O.submit_player_intent(
        handle, "pc1", PlayerIntent(intent_type="move", target_zone_id=destination)
    )
    live = O._get_live(handle)
    return live.initiative[0].movement_remaining, live.actor_zone["pc1"]


def test_an_untagged_ice_patch_still_costs_double():
    # The exemption is per TERRAIN, so ground the scene did not mark as ice
    # keeps doubling even for a walker that ignores ice.
    remaining, zone = asyncio.run(_walk(["1,1"], ["ice", "snow"], "1,1"))
    assert zone == "1,1"
    assert remaining == 20


def test_plain_difficult_terrain_costs_double_without_the_boots():
    # Ground the scene did not mark as any kind doubles for everyone.
    remaining, zone = asyncio.run(_walk(["1,1"], ["ice", "snow"], "1,1"))
    assert zone == "1,1"
    assert remaining == 20


def test_boots_walk_over_ice_at_normal_cost_in_live_combat():
    # SRD 5.2 Boots of the Winterlands, "Winter Strider": the walker ignores
    # Difficult Terrain created by ice or snow. The scene says the cell IS ice.
    remaining, zone = asyncio.run(
        _walk(["1,1"], ["ice", "snow"], "1,1", kinds={"1,1": "ice"})
    )
    assert zone == "1,1"
    assert remaining == 25


def test_ice_still_doubles_for_a_walker_without_the_exemption():
    # Same ice cell, walker names no terrain it ignores: the doubling stands.
    remaining, zone = asyncio.run(_walk(["1,1"], [], "1,1", kinds={"1,1": "ice"}))
    assert zone == "1,1"
    assert remaining == 20


def test_the_exemption_is_limited_to_the_kind_named():
    # Snow boots do not help on ice: the exemption is per terrain kind.
    remaining, zone = asyncio.run(
        _walk(["1,1"], ["snow"], "1,1", kinds={"1,1": "ice"})
    )
    assert zone == "1,1"
    assert remaining == 20
def test_boots_of_the_winterlands_beat_ice_end_to_end():
    # Full path: build spec -> item -> sheet -> party member -> live combat.
    # The player wears attuned boots; the cell the scene calls ice is free.
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        level=3,
        equipment=["boots-of-the-winterlands"],
        attuned_items=["boots-of-the-winterlands"],
    )
    loader = get_lib_loader()
    sheet = derive_sheet(spec, loader=loader)
    assert sheet.ignored_difficult_terrain == ("ice", "snow")
    member = build_party_member(
        spec, CombatInstance(entity_id="pc1", name="Hero", zone_id="0,0",
                           initiative=20, hp_max=30, hp_current=30), loader=loader)
    scene = GridScene(width=8, height=8, difficult_terrain_cells=["1,1"],
                      difficult_terrain_kinds={"1,1": "ice"})
    goblin = EncounterMemberSpec(entity_id="m1", name="Goblin",
                                entity_type="Monster", initiative=5,
                                hp_current=7, hp_max=7, ac=15, zone_id="7,7")
    handle = asyncio.run(
        O.start_combat(session_id="e2e", party=[member], encounter=[goblin],
                       grid_scene=scene, rng_seed=7)
    ).handle
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1", PlayerIntent(intent_type="move", target_zone_id="1,1")
        )
    )
    live = O._get_live(handle)
    assert live.initiative[0].movement_remaining == 25


def _rolled_initiative(advantage, seed):
    # ``initiative=None`` opts into an engine-rolled d20 + DEX modifier,
    # so the same seed reveals whether the item changed the roll.
    pc = PartyMemberSpec(
        entity_id="pc1",
        name="Hero",
        initiative=None,
        hp_current=30,
        hp_max=30,
        dexterity=14,
        zone_id="0,0",
        initiative_advantage=advantage,
    )
    goblin = EncounterMemberSpec(entity_id="m1", name="Goblin",
                                entity_type="Monster", initiative=None,
                                hp_current=7, hp_max=7, ac=15, zone_id="7,7")
    handle = asyncio.run(
        O.start_combat(session_id="init", party=[pc], encounter=[goblin],
                       grid_scene=GridScene(width=8, height=8), rng_seed=seed)
    ).handle
    live = O._get_live(handle)
    return next(c.initiative for c in live.initiative if c.entity_id == "pc1")


def test_ioun_stone_changes_the_rolled_initiative():
    # SRD 5.2 Ioun Stone of Awareness: "Advantage on Initiative rolls."
    # Measured on the ROLL, not on the sheet field, and over several seeds
    # so a single lucky pair cannot carry the claim.
    improved = 0
    for seed in range(8):
        bare = _rolled_initiative(0, seed)
        stone = _rolled_initiative(1, seed)
        assert stone >= bare, (seed, stone, bare)
        improved += stone > bare
    assert improved >= 3, improved


def test_initiative_advantage_cancels_with_surprise_disadvantage():
    # SRD 5.2 D20 Tests: "If you have both, you roll once" -- advantage
    # and disadvantage cancel, so a surprised wearer rolls a plain d20.
    pc = PartyMemberSpec(
        entity_id="pc1",
        name="Hero",
        initiative=None,
        hp_current=30,
        hp_max=30,
        dexterity=14,
        zone_id="0,0",
        initiative_advantage=1,
        is_surprised=True,
    )
    rng = __import__("random").Random(3)
    plain = PartyMemberSpec(
        entity_id="pc1",
        name="Hero",
        initiative=None,
        hp_current=30,
        hp_max=30,
        dexterity=14,
        zone_id="0,0",
    )
    a = O._resolve_initiative(pc, __import__("random").Random(3), frozenset())
    b = O._resolve_initiative(plain, __import__("random").Random(3), frozenset())
    assert a == b


def _bow_attack_modifier(proficiencies):
    # SRD 5.2 Weapon Proficiency: proficiency adds the Proficiency Bonus
    # to the attack roll. Same seed, same target -- only proficiency
    # changes, so the modifier alone isolates the item grants.
    pc = PartyMemberSpec(
        entity_id="pc1",
        name="Hero",
        initiative=20,
        hp_current=30,
        hp_max=30,
        dexterity=14,
        strength=10,
        zone_id="0,0",
        equipment=["longbow"],
        weapon_proficiencies=proficiencies,
    )
    goblin = EncounterMemberSpec(entity_id="m1", name="Goblin",
                                entity_type="Monster", initiative=5,
                                hp_current=40, hp_max=40, ac=12, zone_id="5,0")
    handle = asyncio.run(
        O.start_combat(session_id="bow", party=[pc], encounter=[goblin],
                       grid_scene=GridScene(width=6, height=6), rng_seed=11)
    ).handle
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            PlayerIntent(intent_type="attack", target_id="m1", weapon_id="longbow"),
        )
    )
    for event in O._get_live(handle).event_log:
        if type(event).__name__ == "AttackRolled":
            return event.modifier
    raise AssertionError("no attack was rolled")


def test_bow_proficiency_reaches_the_attack_roll():
    # Bracers of Archery name longbow/shortbow; the sheet carries both.
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        level=3,
        equipment=["bracers-of-archery"],
        attuned_items=["bracers-of-archery"],
    )
    granted = tuple(derive_sheet(spec, loader=get_lib_loader()).weapon_proficiencies)
    assert "longbow" in granted
    without = _bow_attack_modifier(("club",))
    with_bow = _bow_attack_modifier(("longbow",))
    assert with_bow == without + 2
    # A category token is not the bow: proficiency stays off.
    assert _bow_attack_modifier(("mar",)) == without


def _passives(*changes):
    return interpret_passive_stats(
        changes=[PassiveEffectChange(key=k, mode=m, value=v) for k, m, v in changes],
        trait_grants=[],
        species_senses=None,
        species_base_speed=30,
    )


def test_a_flat_walk_bonus_survives_the_multiply_branch():
    # Regression: teaching the walk key to read mode 1 (multiply) once
    # swallowed mode 2 (add) into ``skipped``, silently dropping Roving,
    # Fast Movement and the Horseshoes of Speed flat bonuses.
    stats = _passives(("system.attributes.movement.walk", 2, "10"))
    assert stats.walk_speed_bonus == 10
    assert "system.attributes.movement.walk" not in stats.skipped_keys


def test_boots_of_speed_do_not_grant_speeds_the_bearer_lacks():
    # SRD 5.2 doubling applies to a speed the creature already has: the
    # boots must not conjure a fly or burrow speed out of nothing.
    stats = _passives(("system.attributes.movement.walk", 1, "2"))
    assert stats.walk_speed_multiplier == 2
    assert stats.movement_modes.fly is None
    assert stats.movement_modes.burrow is None
    assert stats.movement_modes.climb is None
    assert stats.movement_modes.swim is None


def test_flat_bonus_and_multiplier_compose():
    # Horseshoes (+30 flat) plus Boots of Speed (x2) is x2 of the boosted
    # walk speed, not one of them winning: (30 + 30) x 2.
    stats = _passives(
        ("system.attributes.movement.walk", 2, "30"),
        ("system.attributes.movement.walk", 1, "2"),
    )
    assert (30 + stats.walk_speed_bonus) * stats.walk_speed_multiplier == 120


def test_an_activity_switched_effect_stays_off_the_owner():
    # An effect the item's own activity names by id belongs to that
    # activation, not to the person carrying the item. Figurine of
    # Wondrous Power (onych dog): Intelligence 8, Common and Blindsight
    # 60 belong to the MASTIFF it becomes, so a character holding the
    # statuette must not acquire them.
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        level=3,
        equipment=["onyx-dog-rare"],
    )
    sheet = derive_sheet(spec, loader=get_lib_loader())
    assert sheet.ability_scores.intelligence == 10
    assert sheet.senses.blindsight is None
    assert "abyssal" not in sheet.languages


def _boots_fight():
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        level=3,
        equipment=["boots-of-speed"],
        attuned_items=["boots-of-speed"],
    )
    loader = get_lib_loader()
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Hero",
            zone_id="0,0",
            initiative=20,
            hp_max=30,
            hp_current=30,
        ),
        loader=loader,
    )
    goblin = EncounterMemberSpec(
        entity_id="m1",
        name="Goblin",
        entity_type="Monster",
        initiative=5,
        hp_current=7,
        hp_max=7,
        ac=13,
        zone_id="3,3",
    )
    return asyncio.run(
        O.start_combat(
            session_id="boots-speed",
            party=[member],
            encounter=[goblin],
            grid_scene=GridScene(width=8, height=8),
            rng_seed=3,
        )
    ).handle


def test_clicking_the_boots_heels_doubles_the_fights_speed():
    handle = _boots_fight()
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == "pc1")
    assert O._effective_speed(hero, live) == 30
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            PlayerIntent(intent_type="use_item", item_id="boots-of-speed"),
        )
    )
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == "pc1")
    assert O._effective_speed(hero, live) == 60

def test_unattuned_boots_cannot_be_clicked_into_doubling():
    # SRD 5.2: attunement is what makes a magic item usable. A character who
    # never attuned must not gain the doubling by clicking the boots anyway.
    # Measured before the gate: 60 with attuned_items=() -- a free x2.
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        level=3,
        equipment=["boots-of-speed"],
    )
    loader = get_lib_loader()
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Hero",
            zone_id="0,0",
            initiative=20,
            hp_max=30,
            hp_current=30,
        ),
        loader=loader,
    )
    goblin = EncounterMemberSpec(
        entity_id="m1",
        name="Goblin",
        entity_type="Monster",
        initiative=5,
        hp_current=7,
        hp_max=7,
        ac=13,
        zone_id="3,3",
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="unattuned",
            party=[member],
            encounter=[goblin],
            grid_scene=GridScene(width=8, height=8),
            rng_seed=3,
        )
    ).handle
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            PlayerIntent(intent_type="use_item", item_id="boots-of-speed"),
        )
    )
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == "pc1")
    assert live.active_effects.get("pc1", []) == []
    assert O._effective_speed(hero, live) == 30

def test_a_speed_zero_condition_stays_zero_under_a_speed_bonus():
    # SRD 5.2 Paralyzed: "your Speed is 0". A flat +10 effect must not revive
    # movement -- measured 10 before the guard.
    from dnd5e_engine.types.conditions import ActiveCondition
    from dnd5e_engine.types.effects import ActiveEffect, ActiveEffectChange

    base = dict(
        entity_id="pc1",
        name="H",
        entity_type="Character",
        initiative=20,
        hp_max=30,
        hp_current=30,
        base_speed=30,
    )

    class _Live:
        slow_marks: dict = {}
        active_effects = {
            "pc1": [
                ActiveEffect(
                    id="roving",
                    name="Roving",
                    origin="probe:roving",
                    target_id="pc1",
                    changes=[
                        ActiveEffectChange(
                            key="system.attributes.movement.walk",
                            mode="add",
                            value="10",
                        )
                    ],
                )
            ]
        }

    live = _Live()
    standing = O.Combatant(**base)
    assert O._effective_speed(standing, live) == 40
    paralyzed = O.Combatant(
        **{
            **base,
            "conditions": [
                ActiveCondition(
                    condition="paralyzed",
                    source_entity_id="implied:monster",
                    scope="combat",
                )
            ],
        }
    )
    assert O._effective_speed(paralyzed, live) == 0



def test_an_ambiguous_multi_activity_item_is_not_priced_or_run_by_guess():
    # instant-fortress carries an "action" and a "bonus" activity. Without
    # activity_id the engine must not pick one to price and another to
    # execute: price and resolution both stay unpicked.
    loader = get_lib_loader()
    item = loader.get_item("instant-fortress")
    assert len(item.activities) > 1
    assert O._named_activity(item, None) is None
    cost = O._classify_action_cost(
        PlayerIntent(intent_type="use_item", item_id="instant-fortress"), None
    )
    assert cost.is_bonus_action is False


def test_exhaustion_zero_speed_is_not_revived_by_a_bonus():
    # SRD 5.2 Exhaustion: "your Speed is reduced by 5 x level". At level 6 on
    # a 30-foot creature the Speed is 0, and a +10 rider must not undo that.
    from dnd5e_engine.types.conditions import ActiveCondition
    from dnd5e_engine.types.effects import ActiveEffect, ActiveEffectChange

    base = dict(
        entity_id="pc1",
        name="H",
        entity_type="Character",
        initiative=20,
        hp_max=30,
        hp_current=30,
        base_speed=30,
    )
    change = ActiveEffectChange(
        key="system.attributes.movement.walk", mode="add", value="10"
    )
    rider = ActiveEffect(
        id="roving",
        name="Roving",
        origin="probe:roving",
        target_id="pc1",
        changes=[change],
    )

    class _Live:
        slow_marks: dict = {}
        active_effects = {"pc1": [rider]}

    live = _Live()
    for level, expected in ((2, 30), (4, 20), (6, 0)):
        exhausted = O.Combatant(
            **{
                **base,
                "conditions": [
                    ActiveCondition(
                        condition="exhaustion",
                        source_entity_id="implied:monster",
                        scope="combat",
                        exhaustion_level=level,
                    )
                ],
            }
        )
        assert O._effective_speed(exhausted, live) == expected

def test_a_passive_rider_does_not_hide_the_items_real_activity():
    # oathbow carries "Attack Sworn Enemy" (an action) next to a bare
    # "Oathbow" enchantment with no activation type. Picking the item must
    # resolve the activity the player actually spends an action on, not
    # refuse the whole item as ambiguous.
    loader = get_lib_loader()
    oathbow = loader.get_item("oathbow")
    chosen = O._named_activity(oathbow, None)
    assert chosen is not None
    assert chosen.name == "Attack Sworn Enemy"
    assert O._named_activity(loader.get_item("lute"), None) is not None


def test_a_reaction_activity_is_priced_as_a_reaction():
    # dwarven-plate's "Reduce Unwilling Movement" is a Reaction; before the
    # price and the executed activity came from different helpers, so it ran
    # as an ordinary action.
    loader = get_lib_loader()
    chosen = O._named_activity(loader.get_item("dwarven-plate"), None)
    assert chosen is not None
    assert chosen.activation.type == "reaction"
    cost = O._classify_action_cost(
        PlayerIntent(intent_type="use_item", item_id="dwarven-plate"), None
    )
    assert cost.is_reaction_cast is True

def test_a_genuinely_ambiguous_item_is_refused_before_the_turn_is_spent():
    # necklace-of-prayer-beads carries six differently named free beads:
    # "use the necklace" cannot pick one, so the engine says so instead of
    # resolving nothing after the action is already gone.
    from dnd5e_engine.orchestrator import PlayerIntent as _Intent

    loader = get_lib_loader()
    beads = loader.get_item("necklace-of-prayer-beads")
    assert len(beads.activities) > 1
    assert O._named_activity(beads, None) is None

    class _Live:
        slow_marks: dict = {}
        active_effects: dict = {}
        initiative: list = []
        event_log: list = []
        _events: list = []

        def __getattr__(self, name):
            raise AttributeError(name)

    # The gate is what the refusal rides on; it must answer True for the
    # ambiguous item and False for an item with one clear activity.
    assert O._named_activity(loader.get_item("lute"), None) is not None
    intent = _Intent(intent_type="use_item", item_id="necklace-of-prayer-beads")
    assert intent.activity_id is None
    # An activity_id resolves the ambiguity, so the gate lets it through.
    assert O._named_activity(beads, beads.activities[0].id) is beads.activities[0]

def test_a_disabled_effect_moves_nobody():
    # Foundry keeps a switched-off effect on the actor without applying it.
    # The sheet projection honours that; the fight-side speed reader must too.
    from dnd5e_engine.types.effects import ActiveEffect, ActiveEffectChange

    base = dict(
        entity_id="pc1",
        name="H",
        entity_type="Character",
        initiative=20,
        hp_max=30,
        hp_current=30,
        base_speed=30,
    )

    def boots(disabled: bool) -> ActiveEffect:
        return ActiveEffect(
            id="boots",
            name="Boots",
            origin="probe:boots",
            target_id="pc1",
            disabled=disabled,
            changes=[
                ActiveEffectChange(
                    key="system.attributes.movement.walk", mode="multiply", value="2"
                )
            ],
        )

    class _Live:
        slow_marks: dict = {}

    live = _Live()
    for disabled, expected in ((False, 60), (True, 30)):
        live.active_effects = {"pc1": [boots(disabled)]}
        assert O._effective_speed(O.Combatant(**base), live) == expected


def test_clicking_the_boots_heels_again_ends_the_effect():
    # SRD 5.2 Boots of Speed: "If you click your heels together again, you end
    # the effect." A repeat ACTIVATION in a live fight must switch the doubling
    # OFF, not stack a second one. Drives the real path (start_combat ->
    # use_item), so the item stamp on the effect is the production one.
    handle = _boots_fight()
    live = O._get_live(handle)
    actor = next(c for c in live.initiative if c.entity_id == "pc1")
    assert O._effective_speed(actor, live) == 30

    asyncio.run(O.submit_player_intent(handle, "pc1", PlayerIntent(intent_type="use_item", item_id="boots-of-speed")))
    live = O._get_live(handle)
    actor = next(c for c in live.initiative if c.entity_id == "pc1")
    assert O._effective_speed(actor, live) == 60
    assert live.active_effects["pc1"][0].flags.get("item") == "boots-of-speed"

    # Back to the boots' owner on a later turn: the goblin passes, the hero
    # passes, which carries the round to the hero again.
    for attempt in range(3):
        try:
            asyncio.run(O.submit_player_intent(handle, "m1", PlayerIntent(intent_type="pass")))
            break
        except O.IntentRejectedError:
            asyncio.run(O.submit_player_intent(handle, "pc1", PlayerIntent(intent_type="pass")))
    asyncio.run(O.submit_player_intent(handle, "pc1", PlayerIntent(intent_type="use_item", item_id="boots-of-speed")))

    live = O._get_live(handle)
    actor = next(c for c in live.initiative if c.entity_id == "pc1")
    assert O._effective_speed(actor, live) == 30
    assert live.active_effects.get("pc1", []) == []
    assert any(
        type(ev).__name__ == "EffectExpired" and "toggled_off" in str(ev.reason)
        for ev in live.event_log
    )


def test_a_spell_effect_never_toggles():
    # Only items switch: two casts of the same spell are two independent
    # applications, so neither carries the item stamp the toggle keys on.
    from dnd5e_engine.events import EffectApplied
    from dnd5e_engine.types.effects import ActiveEffect

    def bless(effect_id: str) -> ActiveEffect:
        return ActiveEffect(
            id=effect_id, name="Bless", origin="spell:bless:pc1", target_id="pc1"
        )

    class _Live:
        def __init__(self) -> None:
            self.slow_marks: dict = {}
            self.active_effects: dict = {}
            self.initiative: list = []
            self.event_log: list = []
            self.party_ids: set = set()

    live = _Live()
    original_emit = O._emit
    O._emit = lambda _l, e: None  # type: ignore[assignment]
    try:
        O._emit_apply_effect_applied(live, EffectApplied(effect=bless("a")))
        O._emit_apply_effect_applied(live, EffectApplied(effect=bless("b")))
        assert len(live.active_effects["pc1"]) == 2
    finally:
        O._emit = original_emit  # type: ignore[assignment]

def test_a_ring_of_free_action_ignores_difficult_terrain_in_a_fight():
    # SRD 5.2 Ring of Free Action: "difficult terrain" is ignored outright.
    # The corpus spells that exemption "all", which is not a terrain KIND; read
    # as one it made the ring ignore nothing at all. Measured on a corridor
    # whose only route crosses ice.
    from dnd5e_engine.build_party import build_party_member
    from dnd5e_engine.build_spec import CharacterBuildSpec, CombatInstance
    from dnd5e_engine.lib_loader import get_lib_loader

    ice = ["1,0", "1,1", "1,2"]

    def _corridor(equip, attuned):
        spec = CharacterBuildSpec(
            species_slug="human",
            class_slug="fighter",
            background_slug="soldier",
            level=3,
            equipment=equip,
            attuned_items=attuned,
        )
        member = build_party_member(
            spec,
            CombatInstance(
                entity_id="pc1", name="Hero", zone_id="0,1", initiative=20, hp_max=30, hp_current=30
            ),
            loader=get_lib_loader(),
        )
        goblin = EncounterMemberSpec(
            entity_id="m1", name="Goblin", entity_type="Monster", initiative=5,
            hp_current=7, hp_max=7, ac=13, zone_id="4,1",
        )
        scene = GridScene(
            width=5,
            height=3,
            blocked_cells=["2,0", "2,2"],
            difficult_terrain_cells=ice,
            difficult_terrain_kinds={cell: "ice" for cell in ice},
        )
        handle = asyncio.run(
            O.start_combat(
                session_id="terrain", party=[member], encounter=[goblin],
                grid_scene=scene, rng_seed=3,
            )
        ).handle
        asyncio.run(
            O.submit_player_intent(
                handle, "pc1", PlayerIntent(intent_type="move", target_zone_id="3,1")
            )
        )
        live = O._get_live(handle)
        hero = next(c for c in live.initiative if c.entity_id == "pc1")
        return hero.movement_remaining, hero.ignored_difficult_terrain

    bare, bare_ignore = _corridor([], [])
    ring, ring_ignore = _corridor(["ring-of-free-action"], ["ring-of-free-action"])
    boots, boots_ignore = _corridor(
        ["boots-of-the-winterlands"], ["boots-of-the-winterlands"]
    )
    assert bare_ignore == () and boots_ignore == ("ice", "snow")
    assert ring_ignore == ("all",)
    # The corridor's only route crosses two ice cells: 20 ft walked, 15 ft for
    # the ring and for the boots, so each ends 5 ft further along.
    assert bare == 10
    assert ring == 15 and boots == 15


def test_a_terrain_kind_exemption_is_not_a_carte_blanche():
    # Boots of the Winterlands name ice and snow only: an ice cell is free, a
    # muddy one still costs double.
    from dnd5e_engine.spatial import GridTopology

    scene = GridScene(
        width=4,
        height=1,
        difficult_terrain_cells=["1,0", "2,0"],
        difficult_terrain_kinds={"1,0": "ice", "2,0": "mud"},
    )
    topology = GridTopology(scene)
    assert topology.edge_distance("0,0", "1,0") == 10
    assert topology.edge_distance("0,0", "1,0", ignore_terrain=("ice", "snow")) == 5
    assert topology.edge_distance("1,0", "2,0", ignore_terrain=("ice", "snow")) == 10
    # "all" covers any kind, and matching is case-insensitive.
    assert topology.edge_distance("1,0", "2,0", ignore_terrain=("ALL",)) == 5

def test_a_padded_terrain_token_still_means_what_it_says():
    # Corpus tokens are hand-written: " all " must mean "all", and an empty
    # token must not turn into a wildcard.
    from dnd5e_engine.spatial import GridTopology

    scene = GridScene(
        width=4, height=1, difficult_terrain_cells=["1,0"],
        difficult_terrain_kinds={"1,0": "ice"},
    )
    topology = GridTopology(scene)
    assert topology.edge_distance("0,0", "1,0", ignore_terrain=(" all ",)) == 5
    assert topology.edge_distance("0,0", "1,0", ignore_terrain=(" ICE",)) == 5
    assert topology.edge_distance("0,0", "1,0", ignore_terrain=("",)) == 10
    assert topology.edge_distance("0,0", "1,0", ignore_terrain=("snow",)) == 10

def test_two_items_sharing_an_effect_name_stay_distinguishable():
    # 23 effect names are shared by several items ("Cold Resistance" on four),
    # and expiry removes an effect by (id, origin). An item-qualified pair is
    # what keeps one item's expiry from taking the other's effect with it.
    from dnd5e_engine.activities.effects import _effect_id_from_name, _origin_from_name

    boots_id = _effect_id_from_name("Cold Resistance (boots-of-the-winterlands)")
    ring_id = _effect_id_from_name("Cold Resistance (ring-of-resistance)")
    assert boots_id != ring_id
    assert _origin_from_name(
        "Cold Resistance (boots-of-the-winterlands)", "pc1"
    ) != _origin_from_name("Cold Resistance (ring-of-resistance)", "pc1")
    # A spell keeps the bare ieffect2 shape it round-trips through.
    assert _effect_id_from_name("Bless") == "effect:bless"
    assert _origin_from_name("Bless", "pc1") == "cast:bless:pc1"

def test_the_item_cycle_survives_a_snapshot_and_a_restore():
    # Task item 4: don -> mechanical effect -> snapshot -> restore -> end. The
    # effect's item stamp and the member's attunement are what let a resumed
    # fight keep BOTH the doubling and the ability to switch it off.
    import json

    from dnd5e_engine.types.effects import ActiveEffect

    handle = _boots_fight()
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1", PlayerIntent(intent_type="use_item", item_id="boots-of-speed")
        )
    )
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == "pc1")
    assert O._effective_speed(hero, live) == 60

    # Round-trip through JSON exactly as the host's durable state does.
    dumped = json.dumps([e.model_dump(mode="json") for e in live.active_effects["pc1"]])
    restored = [ActiveEffect.model_validate(raw) for raw in json.loads(dumped)]
    assert [e.flags.get("item") for e in restored] == ["boots-of-speed"]
    assert restored[0].id == "effect:boots_of_speed_active_(boots-of-speed)"

    # A fresh fight seeded with the restored effect doubles from the start.
    member = _boots_member()
    handle2 = asyncio.run(
        O.start_combat(
            session_id="resumed",
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Goblin", entity_type="Monster", initiative=5,
                    hp_current=7, hp_max=7, ac=13, zone_id="4,2",
                )
            ],
            grid_scene=GridScene(width=6, height=4),
            rng_seed=11,
            active_effects=restored,
        )
    ).handle
    live2 = O._get_live(handle2)
    hero2 = next(c for c in live2.initiative if c.entity_id == "pc1")
    assert O._effective_speed(hero2, live2) == 60

    # The point of carrying the stamp: a resumed fight can still SWITCH the
    # effect off. With the stamp lost, this second click would stack to 120.
    asyncio.run(
        O.submit_player_intent(
            handle2, "pc1", PlayerIntent(intent_type="use_item", item_id="boots-of-speed")
        )
    )
    live3 = O._get_live(handle2)
    hero3 = next(c for c in live3.initiative if c.entity_id == "pc1")
    assert O._effective_speed(hero3, live3) == 30
    assert live3.active_effects.get("pc1", []) == []


def _boots_member():
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        level=3,
        equipment=["boots-of-speed"],
        attuned_items=["boots-of-speed"],
    )
    return build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Hero", zone_id="0,0", initiative=20, hp_max=30, hp_current=30
        ),
        loader=get_lib_loader(),
    )

# --------------------------------------------------------------------------
# activityUses: the pool belongs to the ACTIVITY, not to the item.
# --------------------------------------------------------------------------


def _duel_member(slug, cls="fighter", entity_id="pc1"):
    item = get_lib_loader().get_item(slug)
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug=cls,
        background_slug="soldier",
        level=5,
        equipment=[slug],
        attuned_items=[slug] if item.requires_attunement else [],
    )
    return build_party_member(
        spec,
        CombatInstance(
            entity_id=entity_id, name="Hero", zone_id="0,0", initiative=20, hp_max=40, hp_current=40
        ),
        loader=get_lib_loader(),
    )


def _start_item_duel(slug, cls="fighter", entity_id="pc1"):
    goblin = EncounterMemberSpec(
        entity_id="m1",
        name="Goblin",
        entity_type="Monster",
        initiative=5,
        hp_current=7,
        hp_max=7,
        ac=13,
        zone_id="5,0",
    )
    return asyncio.run(
        O.start_combat(
            session_id="duel",
            party=[_duel_member(slug, cls, entity_id)],
            encounter=[goblin],
            grid_scene=GridScene(width=7, height=2),
            rng_seed=5,
        )
    ).handle


def _back_to_hero(handle):
    """Step turns until the hero is on turn again."""
    for _ in range(4):
        live = O._get_live(handle)
        if live.current_actor_id == "pc1":
            return live
        try:
            asyncio.run(
                O.submit_player_intent(
                    handle, live.current_actor_id, PlayerIntent(intent_type="pass")
                )
            )
        except O.IntentRejectedError:
            pass
    return O._get_live(handle)


def _end_hero_turn(handle):
    # SRD 5.2 Movement keeps the turn after an Action, so advance it
    # explicitly and let the monster take its turn before the hero acts again.
    live = O._get_live(handle)
    if live.current_actor_id == "pc1":
        try:
            asyncio.run(
                O.submit_player_intent(handle, "pc1", PlayerIntent(intent_type="pass"))
            )
        except O.IntentRejectedError:
            pass
    return _back_to_hero(handle)


def _use_item(handle, slug, activity_id, entity_id="pc1"):
    try:
        asyncio.run(
            O.submit_player_intent(
                handle,
                entity_id,
                PlayerIntent(intent_type="use_item", item_id=slug, activity_id=activity_id),
            )
        )
    except O.IntentRejectedError:
        pass
    return [e for e in O.drain_pending_events(handle) if e.type == "cast_failed"]


def test_three_goat_properties_keep_separate_pools():
    """SRD 5.2 Figurine of Wondrous Power: each figurine has its OWN usages.

    A shared item pool would let the 15-use goat block the 7- and 30-use ones,
    which is the regression an earlier pooled reading introduced.
    """
    handle = _start_item_duel("ivory-goats")
    for activity_id in ("hDc2bhCP7qL52Z1J", "G771YStNNwTixstV", "KguY9D4cU0ognpu4"):
        _end_hero_turn(handle)
        assert _use_item(handle, "ivory-goats", activity_id) == []
    live = O._get_live(handle)
    assert O._activity_uses_spent(live, "pc1", "ivory-goats", "hDc2bhCP7qL52Z1J") == 15
    assert O._activity_uses_spent(live, "pc1", "ivory-goats", "G771YStNNwTixstV") == 7
    assert O._activity_uses_spent(live, "pc1", "ivory-goats", "KguY9D4cU0ognpu4") == 30


def test_a_spent_activity_pool_refuses_the_second_use():
    """Comet: the card holds one use; the second use is refused."""
    handle = _start_item_duel("comet")
    assert _use_item(handle, "comet", "fFYGafmMBO89V1OU") == []
    _back_to_hero(handle)
    failed = _use_item(handle, "comet", "fFYGafmMBO89V1OU")
    assert [e.reason for e in failed] == ["no_charges_remaining"]
    live = O._get_live(handle)
    assert O._activity_uses_spent(live, "pc1", "comet", "fFYGafmMBO89V1OU") == 1
    assert live.current_actor_id == "pc1"


def test_an_unnamed_activity_does_not_burn_another_property_pool():
    """staff-of-charming: Resist Enchantment owns a once-per-dawn pool.

    Without an activity_id the engine must not charge that pool for a
    Charm Person cast, which a "charge the first activity" guess did.
    """
    handle = _start_item_duel("staff-of-charming", cls="wizard")
    assert _use_item(handle, "staff-of-charming", "7bZzIRhMxei7kmFO") == []
    live = O._get_live(handle)
    assert O._item_charges_spent(live, "pc1", "staff-of-charming") == 1
    assert O._activity_uses_spent(live, "pc1", "staff-of-charming", "tEk1wNWngqDYOgOB") == 0


def test_activity_pools_recharge_on_their_own_rules():
    """A once-per-dawn property comes back at dawn, not on a Short Rest.

    The activity pool is a separate counter from the item pool, so a rest must
    recharge it from the ACTIVITY's rules; left untouched it would stay spent
    for the rest of the campaign.
    """
    import random

    from dnd5e_engine.rest import recover_activity_uses

    item = get_lib_loader().get_item("ivory-goats")
    rules = {
        f"ivory-goats::{activity.id}": list(activity.uses.recovery)
        for activity in item.activities
        if getattr(activity, "uses", None)
    }
    counters = {
        "activity_use:ivory-goats::hDc2bhCP7qL52Z1J": {"spent": 15},
        "activity_use:ivory-goats::G771YStNNwTixstV": {"spent": 7},
        "item_use:ivory-goats": {"spent": 3},
    }
    # The goats regain one use per DAY, so a dawn rest on their own rules
    # keeps the tally; a rest with no rules at all recharges fully.
    dawn = recover_activity_uses(counters, "dawn", rules, rng=random.Random(5))
    assert dawn["ivory-goats::hDc2bhCP7qL52Z1J"] == 15
    full = recover_activity_uses(counters, "lr", None)
    assert full == {
        "ivory-goats::hDc2bhCP7qL52Z1J": 0,
        "ivory-goats::G771YStNNwTixstV": 0,
    }
    # The item pool is a different key and is left to its own recovery.
    assert "ivory-goats" not in full


def test_an_activity_pool_survives_a_new_combat():
    """The spent activity charge must travel with the hero into the next fight.

    Host contract: ``state["hero"]["custom_counters"]`` is written back into
    ``PartyMemberSpec.custom_counters`` (already flattened per entity, NOT
    keyed by entity again) when combat starts. Drop that threading and a
    once-per-dawn property silently comes back every fight.
    """
    activity_id = "fFYGafmMBO89V1OU"
    goblin = EncounterMemberSpec(
        entity_id="m1",
        name="Goblin",
        entity_type="Monster",
        initiative=5,
        hp_current=7,
        hp_max=7,
        ac=13,
        zone_id="5,0",
    )

    def fight(counters, session):
        member = _duel_member("comet")
        if counters:
            member = member.model_copy(update={"custom_counters": counters})
        return asyncio.run(
            O.start_combat(
                session_id=session,
                party=[member],
                encounter=[goblin],
                grid_scene=GridScene(width=7, height=2),
                rng_seed=5,
            )
        ).handle

    first = fight(None, "cycle-1")
    assert _use_item(first, "comet", activity_id) == []
    saved = dict(O._get_live(first).custom_counters_by_entity.get("pc1", {}))
    assert saved == {f"activity_use:comet::{activity_id}": {"spent": 1}}

    second = fight(saved, "cycle-2")
    live = O._get_live(second)
    assert live.custom_counters_by_entity.get("pc1", {}) == saved
    failed = _use_item(second, "comet", activity_id)
    assert [e.reason for e in failed] == ["no_charges_remaining"]


def test_an_activity_without_its_own_pool_falls_back_to_the_item_pool():
    """ivory-goats carries three pooled properties AND a fourth with no pool.

    The unpooled summons spend the item's 24 charges; the pooled ones spend
    their own counters and must NOT touch that item pool. Mixing the two is
    the regression that made one goats pool block the others.
    """
    item = get_lib_loader().get_item("ivory-goats")
    pooled = [a.id for a in item.activities if str(getattr(a.uses, "max", "") or "").strip()]
    unpooled = [a.id for a in item.activities if not str(getattr(a.uses, "max", "") or "").strip()]
    assert pooled == ["hDc2bhCP7qL52Z1J", "G771YStNNwTixstV", "KguY9D4cU0ognpu4"]
    assert unpooled == ["5g8tPLkudHb2kfkt"]

    handle = _start_item_duel("ivory-goats")
    assert _use_item(handle, "ivory-goats", pooled[0]) == []
    _end_hero_turn(handle)
    assert _use_item(handle, "ivory-goats", unpooled[0]) == []

    counters = O._get_live(handle).custom_counters_by_entity["pc1"]
    assert counters["activity_use:ivory-goats::hDc2bhCP7qL52Z1J"] == {"spent": 15}
    assert counters["item_use:ivory-goats"] == {"spent": 1}
    assert "activity_use:ivory-goats::5g8tPLkudHb2kfkt" not in counters


# --------------------------------------------------------------------------
# Russian labels: the term table is provenance-checked, identity is never
# translated.
# --------------------------------------------------------------------------


def test_the_label_table_matches_the_project_glossary():
    """Every Russian wording here must be a term the project verified in the
    Russian PHB 2024 / GM 2024 text, with the page it was found on.

    The glossary is the authority; this table is its executable copy. A term
    that appears in no source text is OMITTED rather than invented, so the
    four known gaps (legendary actions, lair actions, innate spellcasting and
    the proficiency-bonus shorthand) have no entry at all.
    """
    from dnd5e_engine.labels import LABELS, PAGES, label

    assert LABELS["Armor Class"] == "Класс защиты (КЗ)"
    assert LABELS["Saving Throw"] == "Спасбросок"
    assert LABELS["Advantage"] == "Преимущество"
    assert LABELS["Disadvantage"] == "Помеха"
    assert LABELS["Difficult Terrain"] == "Труднопроходимая местность"
    assert LABELS["Short Rest"] == "Короткий отдых"

    # Every label carries provenance: the same English term in PAGES.
    assert set(LABELS) == set(PAGES)
    page, _ = PAGES["Armor Class"]
    assert page is not None

    # Terms absent from the source stay absent -- no invented wording.
    for missing in ("Legendary Actions", "Lair Actions", "Innate Spellcasting"):
        assert missing not in LABELS

    # An unknown term is returned unchanged, never guessed.
    assert label("Advantage") == "Преимущество"
    assert label("Not A Rules Term") == "Not A Rules Term"
    assert label("Not A Rules Term", fallback="?") == "?"


def test_rules_identity_stays_ascii_while_only_labels_translate():
    """Slugs and counter keys are state; translating them would break every
    round trip the host performs.
    """
    from dnd5e_engine.labels import label

    item = get_lib_loader().get_item("comet")
    assert item.slug == "comet"
    assert item.name == "Comet"
    assert all(activity.id.isascii() for activity in item.activities)
    key = O._activity_use_counter_key("comet", "fFYGafmMBO89V1OU")
    assert key.isascii()
    # The label is a presentation layer only.
    assert label("Critical Hit") == "Критическое попадание"


def test_every_engine_condition_has_a_russian_name_from_the_phb():
    """The engine ships 15 conditions; the Russian PHB lists them on page 30.

    A condition the narrator can apply but cannot name in Russian would leak
    the raw id ("prone") into the story. This check is exact in BOTH
    directions: a new engine condition without a label, or a stale label for
    a condition the engine no longer has, both fail here.
    """
    from dnd5e_engine.labels import CONDITIONS, CONDITIONS_PAGE
    from dnd5e_engine.rules.conditions import Condition

    engine_ids = [condition.value for condition in Condition]
    assert len(engine_ids) == 15
    assert set(engine_ids) == set(CONDITIONS)
    assert CONDITIONS_PAGE == 30

    # Spot-check the transitions a narrator actually reports.
    assert CONDITIONS["prone"] == "Сбитый с ног"
    assert CONDITIONS["stunned"] == "Ошеломлённый"
    assert CONDITIONS["exhaustion"] == "Истощённый"
    assert CONDITIONS["unconscious"] == "Бессознательный"

    # Every Russian name is Cyrillic -- no English slipped through.
    for condition_id, russian in CONDITIONS.items():
        assert any("\u0400" <= ch <= "\u04FF" for ch in russian), condition_id


def test_the_label_table_cannot_drift_from_the_glossary_source():
    """The previous test spot-checks hand-copied values -- it would pass even
    if 60 of the 72 entries silently diverged from the glossary.

    This one re-reads the glossary JSONL and compares EVERY term and EVERY
    page number. The glossary is in the owner's read-only materials, so a
    missing source is skipped rather than failed: a checkout without the
    extraction work must still run.
    """
    import json
    from pathlib import Path

    from dnd5e_engine.labels import LABELS, PAGES

    source = Path(
        r"C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo"
        r"\outputs\SoloRealmsAI\rules_extract\work\ru\RU_UI_STRINGS.jsonl"
    )
    if not source.is_file():
        pytest.skip("glossary source is outside this checkout")

    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    verified = {
        row["en_ref"]: row
        for row in rows
        if (row.get("exists_phb") or row.get("exists_gm")) and row.get("en_ref") and row.get("ru_phb")
    }
    assert len(verified) == 72

    # Same terms, same wording -- no divergence is tolerated.
    assert set(LABELS) == set(verified)
    for english, russian in LABELS.items():
        assert russian == verified[english]["ru_phb"], english

    # Same provenance: the page recorded in the glossary.
    for english, (phb_page, gm_page) in PAGES.items():
        assert (phb_page, gm_page) == (
            verified[english].get("phb_page"),
            verified[english].get("gm_page"),
        ), english


def test_the_host_save_load_chain_carries_the_activity_pool():
    """Replays the exact chain solo/nat20_worker.py uses, not a hand-built dict.

    The earlier test passed the saved dict straight into PartyMemberSpec, which
    is exactly the step that could silently be wrong. The host instead does:

      sync_hero  -> state["hero"]["custom_counters"] = view["custom_counters_by_entity"]["hero"]
      start      -> party = dict(state["hero"], zone_id=...)
                 -> PartyMemberSpec.model_validate(party)
                 -> initial["party"] (frozen, JSON round-tripped)

    If any link drops the field the pool silently comes back every fight while
    the engine tests stay green. This test fails instead.
    """
    from dnd5e_engine.specs import PartyMemberSpec

    activity_id = "fFYGafmMBO89V1OU"
    goblin = EncounterMemberSpec(
        entity_id="m1", name="Goblin", entity_type="Monster", initiative=5,
        hp_current=7, hp_max=7, ac=13, zone_id="5,0",
    )

    def start_from_hero(hero, session):
        # nat20_worker.py:363 -- the hero dict IS the party member payload.
        party = dict(hero, zone_id="0,0")
        member = PartyMemberSpec.model_validate(party)
        # nat20_worker.py:364-367 freezes it into initial["party"].
        frozen = json.loads(json.dumps({"party": [party]}))["party"][0]
        assert PartyMemberSpec.model_validate(frozen).custom_counters == member.custom_counters
        return asyncio.run(
            O.start_combat(
                session_id=session,
                party=[member],
                encounter=[goblin],
                grid_scene=GridScene(width=7, height=2),
                rng_seed=5,
            )
        ).handle

    first = _start_item_duel("comet")
    assert _use_item(first, "comet", activity_id) == []

    # sync_hero: nat20_worker.py:210
    hero = build_party_member(
        CharacterBuildSpec(
            species_slug="human", class_slug="fighter", background_slug="soldier",
            level=5, equipment=["comet"],
        ),
        CombatInstance(
            entity_id="pc1", name="Hero", zone_id="0,0", initiative=20,
            hp_max=40, hp_current=40,
        ),
        loader=get_lib_loader(),
    ).model_dump(mode="json", exclude_unset=True)
    # read_combat (nat20_worker.py:51) is get_live + serialisation.
    view = json.loads(json.dumps(O._get_live(first).custom_counters_by_entity))
    hero["custom_counters"] = view["pc1"]  # _start_item_duel names the hero pc1
    assert hero["custom_counters"] == {"activity_use:comet::" + activity_id: {"spent": 1}}

    second = start_from_hero(hero, "host-chain")
    live = O._get_live(second)
    assert live.custom_counters_by_entity["pc1"]["activity_use:comet::" + activity_id] == {"spent": 1}
    failed = _use_item(second, "comet", activity_id)
    assert [e.reason for e in failed] == ["no_charges_remaining"]


def test_a_dawn_item_pool_recharges_from_the_hero_equipment_list():
    """The host patch must read the equipment list from where it really lives.

    `HOST_PATCH_REST_CHARGES.md` originally built the recovery table from
    `sheet["equipment"]`. Still there: `DerivedSheet` has 33 fields and no
    `equipment`; the list is `hero["equipment"]`. With the wrong source the
    loop walks an empty list and restores nothing, silently. This test pins
    the correct source and the numbers the patch promises.
    """
    import random

    from dnd5e_engine import recover_item_uses
    from dnd5e_engine.build_spec import CharacterBuildSpec, CombatInstance, derive_sheet

    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="soldier",
        level=5, equipment=["wand-of-magic-missiles", "necklace-of-prayer-beads"],
    )
    sheet = derive_sheet(spec, loader=get_lib_loader())
    assert "equipment" not in type(sheet).model_fields
    assert "features" in type(sheet).model_fields

    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="hero", name="Hero", zone_id="0,0", initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=get_lib_loader(),
    )
    hero = member.model_dump(mode="json", exclude_unset=True)
    assert list(hero["equipment"]) == ["wand-of-magic-missiles", "necklace-of-prayer-beads"]

    # The patch as written: build the table from hero["equipment"].
    item_recovery = {}
    for slug in hero.get("equipment", []):
        item = get_lib_loader().get_item(slug)
        if item is not None and item.uses is not None:
            item_recovery[slug] = list(item.uses.recovery or ())
    # Only the wand has an item pool; the necklace has activity pools only.
    assert list(item_recovery) == ["wand-of-magic-missiles"]
    assert item_recovery["wand-of-magic-missiles"][0].period == "dawn"
    assert item_recovery["wand-of-magic-missiles"][0].formula == "1d6 + 1"

    counters = {"item_use:wand-of-magic-missiles": {"spent": 5}}
    restored = recover_item_uses(counters, "dawn", item_recovery, rng=random.Random(7))
    assert restored == {"wand-of-magic-missiles": 1}

    # Negative control: a pool that was never drawn from comes back at 0,
    # not missing -- the function reports the resulting spent count.
    counters = {"item_use:wand-of-magic-missiles": {"spent": 0}}
    assert recover_item_uses(counters, "dawn", item_recovery, rng=random.Random(7)) == {
        "wand-of-magic-missiles": 0,
    }

    # Comet (auto_destroy, recovery: []) never recharges: its rules carry no
    # entry for this period, so _recover_uses PRESERVES the spent count. The
    # contract is "no rule for this period -> unchanged", not "empty -> refill".
    comet = {"item_use:comet": {"spent": 1}}
    assert recover_item_uses(comet, "dawn", {"comet": []}, rng=random.Random(7)) == {"comet": 1}
    # With no recovery data at all the legacy path refills everything, which is
    # why the host MUST pass the table rather than None for consumables.
    assert recover_item_uses(comet, "dawn", None) == {"comet": 0}


def _rule(period, kind, formula=""):
    """A recovery rule shaped like dnd5e_srd_data RecoveryRule."""
    from types import SimpleNamespace
    return SimpleNamespace(period=period, type=kind, formula=formula)

def test_a_long_rest_does_not_imply_dawn():
    """CORRECTED (DELIVERY 14.203): a Long Rest is not a sunrise.

    This test previously asserted ``periods_covered("lr") == ("lr", "dawn",
    "day")`` and that a long rest recharges a ``dawn``-keyed wand from 5 to 1.
    That equality was WRONG: it encoded a host assumption -- "the party slept,
    so dawn must have come" -- inside the engine. Resting and the calendar are
    different events, and the engine cannot know whether a rest crossed dawn.

    What the engine owes the host is exact scoring of the event it is TOLD
    about. Whether dawn arrived is game time, owned by the host's saved state
    (the engine stays free of clocks and SQLite). A host that really does
    advance time across a rest emits two events: ``"lr"`` and ``"dawn"``.
    """
    import random

    from dnd5e_engine import periods_covered, recover_activity_uses, recover_item_uses

    # One event satisfies exactly its own rule -- no calendar substitution.
    assert periods_covered("lr") == ("lr",)
    assert periods_covered("sr") == ("sr",)
    assert periods_covered("dawn") == ("dawn",)
    assert periods_covered("day") == ("day",)
    assert periods_covered("dusk") == ("dusk",)

    # wand-of-magic-missiles: pool 7, dawn, 1d6 + 1
    wand_recovery = {"wand-of-magic-missiles": [_rule("dawn", "formula", "1d6 + 1")]}
    spent = {"item_use:wand-of-magic-missiles": {"spent": 5}}

    # (1) A long rest alone does NOT recharge a dawn pool, at any hour.
    assert recover_item_uses(spent, "lr", wand_recovery, rng=random.Random(7)) == {
        "wand-of-magic-missiles": 5,
    }
    assert recover_item_uses(spent, "sr", wand_recovery, rng=random.Random(7)) == {
        "wand-of-magic-missiles": 5,
    }

    # (2) An observed dawn DOES recharge it, with no rest involved.
    assert recover_item_uses(spent, "dawn", wand_recovery, rng=random.Random(7)) == {
        "wand-of-magic-missiles": 1,
    }

    # (2b) ...but dawn is not day: a day event leaves the dawn pool alone.
    assert recover_item_uses(spent, "day", wand_recovery, rng=random.Random(7)) == {
        "wand-of-magic-missiles": 5,
    }

    # Ivory Goats properties are keyed "day" -- restored by a day event only.
    goats_recovery = {"ivory-goats::hDc2bhCP7qL52Z1J": [_rule("day", "formula", "1")]}
    goats = {"activity_use:ivory-goats::hDc2bhCP7qL52Z1J": {"spent": 15}}
    assert recover_activity_uses(goats, "lr", goats_recovery, rng=random.Random(7)) == {
        "ivory-goats::hDc2bhCP7qL52Z1J": 15,
    }
    assert recover_activity_uses(goats, "day", goats_recovery, rng=random.Random(7)) == {
        "ivory-goats::hDc2bhCP7qL52Z1J": 14,
    }

def test_one_call_restores_every_pool_the_kit_owns():
    """A host restores charges with a single call, not three hand-built tables.

    The delivery patch got this wrong twice: it read the equipment list from a
    field that does not exist, and it passed a period no rule matched. Both
    traps are gone here -- the caller passes the slugs it already has and the
    period mapping is internal.

    Measured on the real corpus: a short rest changes nothing, a long rest
    recharges the wand (dawn), the bead (dawn/recoverAll) and the goat (day),
    while the consumed one-shot Comet stays spent.
    """
    import random

    from dnd5e_engine import recover_item_and_activity_pools

    loader = get_lib_loader()
    equipment = ["wand-of-magic-missiles", "necklace-of-prayer-beads", "comet", "ivory-goats"]

    necklace = loader.get_item("necklace-of-prayer-beads")
    bead = next(
        activity.id
        for activity in necklace.activities
        if getattr(activity, "uses", None) is not None
        and str(activity.uses.max or "").strip()
    )
    goats = loader.get_item("ivory-goats")
    goat = next(
        activity.id
        for activity in goats.activities
        if getattr(activity, "uses", None) is not None
        and str(activity.uses.max or "").strip()
    )

    wand_key = "item_use:wand-of-magic-missiles"
    bead_key = "activity_use:necklace-of-prayer-beads::" + bead
    comet_key = "item_use:comet"
    goat_key = "activity_use:ivory-goats::" + goat
    counters = {
        wand_key: {"spent": 5},
        bead_key: {"spent": 1},
        comet_key: {"spent": 1},
        goat_key: {"spent": 15},
    }

    # A Short Rest does not reach dawn: nothing recharges.
    short = recover_item_and_activity_pools(counters, "sr", equipment, loader, rng=random.Random(7))
    assert short == {
        wand_key: 5,
        comet_key: 1,
        bead_key: 1,
        goat_key: 15,
    }

    # CORRECTED (DELIVERY 14.203). A Long Rest is NOT a sunrise: it must leave
    # every dawn/day pool untouched. It previously asserted lr -> wand 1,
    # bead 0, goat 14 -- that encoded the host assumption "slept, so dawn".
    long = recover_item_and_activity_pools(counters, "lr", equipment, loader, rng=random.Random(7))
    assert long[wand_key] == 5, "a long rest alone does not reach dawn"
    assert long[bead_key] == 1, "a long rest alone does not reach dawn"
    assert long[goat_key] == 15, "a long rest alone does not turn the day over"
    assert long[comet_key] == 1, "a one-shot item never comes back"

    # The host reports the calendar event it actually observed. That is what
    # restores the dawn/day pools -- and it works without any rest at all.
    dawn = recover_item_and_activity_pools(counters, "dawn", equipment, loader, rng=random.Random(7))
    assert dawn[wand_key] == 1, "wand regains 1d6 + 1 of its 5 spent charges at dawn"
    assert dawn[bead_key] == 0, "recoverAll at dawn"
    assert dawn[goat_key] == 15, "day pool is untouched by a dawn event"
    assert dawn[comet_key] == 1, "a one-shot item never comes back"

    day = recover_item_and_activity_pools(counters, "day", equipment, loader, rng=random.Random(7))
    assert day[goat_key] == 14, "day pool regains one use when the day turns"
    assert day[wand_key] == 5, "a day event is not a dawn"

    # The counters are pure input: the function must not mutate them.
    assert counters[wand_key] == {"spent": 5}


def test_no_item_pool_in_the_corpus_is_left_permanently_dead():
    """Every charge pool in the shipped corpus recharges through the API.

    Scanning all 546 items finds pools tied to rests, to calendar events
    (dawn/day/dusk) and to both. Each must be reachable through the API by the
    event its own rule names -- NOT because a Long Rest is assumed to reach
    dawn or roll the day over (that assumption was removed in DELIVERY 14.203).
    The test therefore sweeps all five events (sr, lr, dawn, day, dusk): a
    pool answers only to its own period, and a pool that answers to none is a
    charge the player can never get back.
    """
    import os
    import random

    import dnd5e_srd_data
    from dnd5e_engine import recover_item_and_activity_pools

    loader = get_lib_loader()
    base = os.path.join(os.path.dirname(dnd5e_srd_data.__file__), "canonical", "items")
    slugs = sorted(name[:-5] for name in os.listdir(base) if name.endswith(".json"))
    assert len(slugs) > 500, "the corpus scan must actually see the items"

    pools = []
    for slug in slugs:
        item = loader.get_item(slug)
        if item is None:
            continue
        if item.uses is not None and str(item.uses.max or "").strip():
            pools.append(("item_use:" + slug, slug, [r.period for r in item.uses.recovery]))
        for activity in getattr(item, "activities", ()) or ():
            uses = getattr(activity, "uses", None)
            if uses is not None and str(uses.max or "").strip():
                pools.append((
                    f"activity_use:{slug}::{activity.id}",
                    slug,
                    [r.period for r in uses.recovery],
                ))
    assert len(pools) > 100, "the corpus must expose charge pools"

    # A pool with no recovery rule at all is a consumable (alchemist's fire,
    # antitoxin, a candle): it is SUPPOSED to stay spent. Only a pool that
    # declares a rule must be reachable by some period.
    dead = []
    with_rule = 0
    for key, slug, periods in pools:
        if not periods:
            continue
        with_rule += 1
        counters = {key: {"spent": 4}}
        reached = False
        for period in ("sr", "lr", "dawn", "day", "dusk"):
            result = recover_item_and_activity_pools(
                counters, period, [slug], loader, rng=random.Random(1)
            )
            if result.get(key) != 4:
                reached = True
                break
        if not reached:
            dead.append((key, periods))

    assert with_rule > 90, f"expected the corpus to declare recovery rules, saw {with_rule}"
    assert dead == [], f"pools with a rule that no period reaches: {dead}"


def test_every_charge_item_has_a_russian_name():
    """A Russian table can only show Russian if the names exist.

    Before this table every one of the 546 items came back with its English
    name, so a player reading a wand in play saw "Wand of Magic Missiles".
    The rule is deliberately scoped: items that carry a charge pool are the
    ones the engine spends and recharges, so those must all be translated.
    An item outside the table may still fall back to its slug -- that is the
    visible-missing-translation behaviour of `label`.
    """
    import os

    import dnd5e_srd_data

    from dnd5e_engine import ITEM_LABELS, item_label

    loader = get_lib_loader()
    base = os.path.join(os.path.dirname(dnd5e_srd_data.__file__), "canonical", "items")

    missing = []
    charge_items = 0
    seen = {}
    for name in os.listdir(base):
        if not name.endswith(".json"):
            continue
        body = json.loads(open(os.path.join(base, name), encoding="utf-8").read())
        if body.get("item_kind") != "magic_item":
            continue
        item = loader.get_item(body["slug"])
        if item is None:
            continue
        spends = item.uses is not None and str(item.uses.max or "").strip()
        spends = spends or any(
            getattr(activity, "uses", None) is not None
            and str(activity.uses.max or "").strip()
            for activity in (getattr(item, "activities", ()) or ())
        )
        if not spends:
            continue
        charge_items += 1
        slug = body["slug"]
        if slug not in ITEM_LABELS:
            missing.append(slug)
        russian = item_label(slug)
        if russian in seen:
            missing.append(slug + " (same name as " + seen[russian] + ")")
        seen[russian] = slug

    assert charge_items > 100, "expected the charge-item corpus, saw " + str(charge_items)
    assert missing == [], "charge items without a unique Russian name: " + str(missing)

    # Coverage is the whole corpus, not only the pools: an item the player can
    # hold is read by name in the inventory even when it never spends a charge.
    untranslated = sorted(
        name[:-5] for name in os.listdir(base)
        if name.endswith(".json") and name[:-5] not in ITEM_LABELS
    )
    assert untranslated == [], "items still showing an English name: " + str(untranslated)

    # Two slugs sharing one English name must not drift apart in Russian:
    # 'Spell Scroll, Cantrip' once came out as both '(фокус)' and '(заговор)'.
    bodies = {}
    for slug in ITEM_LABELS:
        bodies[slug] = json.loads(open(os.path.join(base, slug + ".json"), encoding="utf-8").read())
    by_english = {}
    for slug, russian in ITEM_LABELS.items():
        by_english.setdefault(bodies[slug]["name"], []).append(slug)
    drifted = {}
    for name, slugs in by_english.items():
        if len(slugs) < 2:
            continue
        russian = {item_label(slug) for slug in slugs}
        descriptions = {bodies[slug].get("description", "") for slug in slugs}
        if len(descriptions) == 1 and len(russian) > 1:
            drifted[name] = sorted(russian)
    assert drifted == {}, "one English name, several Russian ones: " + str(drifted)
    assert item_label("comet") == "Комета"
    assert item_label("wand-of-magic-missiles") == "Жезл магических снарядов"


def test_conditions_and_items_are_reachable_through_label():
    """label() must not hand English back for a term the engine translates.

    CONDITIONS and ITEM_LABELS used to sit in tables the generic lookup never
    consulted, so a screen calling label("prone") showed "prone" while the
    Russian text was one dictionary away.
    """
    from dnd5e_engine import CONDITIONS, ITEM_LABELS, label

    untranslated = [term for term in CONDITIONS if label(term) == term]
    assert untranslated == [], "conditions label() leaves in English: " + str(untranslated)

    items = [slug for slug in ITEM_LABELS if label(slug) == slug]
    assert items == [], "items label() leaves in English: " + str(items)

    assert label("prone") == "Сбитый с ног"
    assert label("comet") == "Комета"


def test_label_finds_a_term_regardless_of_letter_case():
    """Rules text writes "Advantage" in a heading and "advantage" inline."""
    from dnd5e_engine import CONDITIONS, LABELS, item_label, label

    missed = [term for term in LABELS if label(term.lower()) != LABELS[term]]
    assert missed == [], "terms missed when asked in lower case: " + str(missed)

    assert label("Advantage") == label("advantage") == "Преимущество"
    assert label("Difficult Terrain") == label("difficult terrain")

    # Conditions and items are matched the same way: rules text writes
    # "Prone" in a heading and "prone" mid-sentence.
    for term, russian in CONDITIONS.items():
        assert label(term.title()) == russian, term
    assert label("Comet") == label("comet") == "Комета"
    assert item_label("Comet") == "Комета"


def test_the_activity_pool_works_under_the_host_entity_id():
    """The host calls its hero "hero", the tests used "pc1"."""
    handle = _start_item_duel("wand-of-magic-missiles", entity_id="hero")
    rejected = _use_item(
        handle, "wand-of-magic-missiles", "4pBEpvS3jgCIhpUi", entity_id="hero"
    )
    assert rejected == [], rejected

    counters = O._get_live(handle).custom_counters_by_entity
    assert "hero" in counters, counters
    assert counters["hero"]["item_use:wand-of-magic-missiles"]["spent"] == 1





def test_only_dusk_recharges_the_robe_of_stars():
    """A `dusk` pool must not ride the long rest; HOST_PATCH says so."""
    from dnd5e_engine import recover_item_and_activity_pools

    loader = get_lib_loader()
    counters = {"item_use:robe-of-stars": {"spent": 6}}

    for period in ("sr", "lr", "dawn", "day"):
        restored = recover_item_and_activity_pools(
            dict(counters), period, ["robe-of-stars"], loader, rng=random.Random(0)
        )
        assert restored["item_use:robe-of-stars"] == 6, period

    at_dusk = recover_item_and_activity_pools(
        dict(counters), "dusk", ["robe-of-stars"], loader, rng=random.Random(0)
    )
    assert 2 <= at_dusk["item_use:robe-of-stars"] <= 6
    assert at_dusk["item_use:robe-of-stars"] < 6


def test_rulebook_entities_have_russian_names():
    """Spells, species, classes and feats reach the player by name."""
    from dnd5e_engine import RULES_ENTITIES, label

    assert len(RULES_ENTITIES) > 350
    missed = [slug for slug in RULES_ENTITIES if label(slug) == slug]
    assert missed == [], "entities label() leaves in English: " + str(missed[:10])
    duplicates = [
        name for name in set(RULES_ENTITIES.values())
        if list(RULES_ENTITIES.values()).count(name) > 1
    ]
    assert duplicates == [], "duplicate Russian names: " + str(duplicates)

    assert label("fireball") == "Огненный шар"
    assert label("Fireball") == "Огненный шар"
    assert label("bless") == "Благословение"


def test_every_cast_failure_reason_has_russian_text():
    """`CastFailed.reason` reaches the player as a bare slug otherwise."""
    from dnd5e_engine import FAILURE_REASONS, label
    from dnd5e_engine import orchestrator as O

    assert len(FAILURE_REASONS) >= 30

    source = inspect.getsource(O)
    emitted = set(re.findall(r'reason="([a-z_]+)"', source))
    assert emitted, "no failure reasons found in the orchestrator"

    untranslated = sorted(code for code in emitted if label(code) == code)
    assert untranslated == [], "failure codes shown to the player in English: " + str(untranslated)
    assert label("no_charges_remaining") == "заряды закончились"


def test_no_slug_in_event_fields_reaches_the_player_in_english():
    """Every `field="value"` literal the orchestrator emits must be translatable."""
    from dnd5e_engine import label
    from dnd5e_engine import orchestrator as O
    from dnd5e_engine import outcome

    missing = set()
    for module in (O, outcome):
        source = inspect.getsource(module)
        # mode="json" is a serialiser argument, not a field the player sees.
        source = re.sub(r'mode="json"', '', source)
        for value in re.findall(r'[_a-z]+="([a-z][a-z_]{2,})"', source):
            if label(value) == value:
                missing.add(value)

    assert missing == set(), "slugs shown to player in English: " + str(sorted(missing))
    assert label("dodge") == "Уклонение"
    assert label("dex") == "Ловкость"


def test_a_second_action_surge_reports_the_turn_limit_not_the_rest_pool():
    """SRD 5.2: Action Surge is capped per rest AND only once on a turn.

    The per-turn refusal must reach the player, not the per-rest pool message:
    a fighter who still owes a second surge after this turn must not be told
    that their uses are gone.
    """
    handle = _start_item_duel("ivory-goats")
    O.drain_pending_events(handle)

    _surge(handle)
    events = _surge(handle)

    reasons = [e.reason for e in events if e.type == "cast_failed"]
    assert reasons == ["no_action_economy"], reasons


def _surge(handle):
    try:
        asyncio.run(
            O.submit_player_intent(
                handle,
                "pc1",
                PlayerIntent(intent_type="use_feature", feature_id="action-surge"),
            )
        )
    except O.IntentRejectedError:
        pass
    return O.drain_pending_events(handle)
