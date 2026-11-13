"""SRD 5.2: заряды предмета — расход, повторное применение и исчерпание."""

from __future__ import annotations

import asyncio
import itertools

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

_LOADER = BundledAssetLoader()
_SESSION = itertools.count()
_HOOD = "l9IG07yOeWzPOpWB"  # Cloak of Invisibility, "Raise Hood"


def _cloak_fight():
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="wizard",
        background_slug="acolyte",
        level=5,
        equipment=["cloak-of-invisibility"],
        attuned_items=["cloak-of-invisibility"],
        ability_scores=AbilityScores(
            strength=14,
            dexterity=16,
            constitution=14,
            intelligence=18,
            wisdom=12,
            charisma=10,
        ),
    )
    pc = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Герой",
            zone_id="0,0",
            initiative=20,
            hp_max=600,
            hp_current=600,
        ),
        loader=_LOADER,
    )
    result = asyncio.run(
        O.start_combat(
            session_id=f"cloak-{next(_SESSION)}",
            party=[pc],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1",
                    name="Болван",
                    entity_type="Monster",
                    initiative=1,
                    hp_current=999,
                    hp_max=999,
                    ac=12,
                    zone_id="8,8",
                )
            ],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=3,
        )
    )
    O.drain_pending_events(result.handle)
    return result.handle


def _raise_hood(handle):
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(
                intent_type="use_item", item_id="cloak-of-invisibility", activity_id=_HOOD
            ),
        )
    )
    return O.drain_pending_events(handle)


def _spent(handle):
    return O._item_charges_spent(O._get_live(handle), "pc1", "cloak-of-invisibility")


def _next_turn(handle):
    asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="pass")))
    O.drain_pending_events(handle)
    asyncio.run(O.advance_monster_turn(handle))
    O.drain_pending_events(handle)


def test_raising_the_hood_spends_one_of_three_charges():
    handle = _cloak_fight()
    assert _spent(handle) == 0
    events = _raise_hood(handle)
    assert _spent(handle) == 1
    assert any(getattr(e, "type", "") == "condition_applied" for e in events)


def test_the_pool_runs_out_and_the_engine_names_the_reason():
    # «This property of the cloak can be used three times per day».
    handle = _cloak_fight()
    for _ in range(3):
        events = _raise_hood(handle)
        assert any(getattr(e, "type", "") == "condition_applied" for e in events)
        _next_turn(handle)
    assert _spent(handle) == 3
    events = _raise_hood(handle)
    assert [getattr(e, "reason", None) for e in events if e.type == "cast_failed"] == [
        "no_charges_remaining"
    ]
    assert _spent(handle) == 3, "отказ не должен тратить несуществующий заряд"


def test_the_player_learns_why_in_russian():
    from dnd5e_engine.labels import FAILURE_REASONS

    assert FAILURE_REASONS["no_charges_remaining"]


def test_each_charge_also_costs_the_action():
    # Второй подряд взмах в тот же ход невозможен: предмет не обходит экономику
    # действий (§6), хотя заряда ещё два.
    handle = _cloak_fight()
    _raise_hood(handle)
    try:
        _raise_hood(handle)
    except O.IntentRejectedError as error:
        assert "no_action_economy" in str(error)
    else:
        raise AssertionError("второе действие в тот же ход не может пройти")
    assert _spent(handle) == 1
