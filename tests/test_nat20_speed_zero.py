"""Требование 6: состояния со «Speed 0» и их отказ в перемещении (§7 — через
публичный интерфейс хода, а не прямой вызов).

SRD 5.2, глоссарий: «Speed 0. Your Speed is 0 and can't increase» — Grappled,
Restrained, Paralyzed, Petrified, Unconscious. В редакции 2024 Grappled
ДЕЙСТВИТЕЛЬНО обнуляет Speed (в 2014 было ограничение на перемещение, не на
скорость). Prone скорость не обнуляет — он ограничивает СПОСОБ движения,
поэтому в этом семействе его нет.

Важно: отказ обязан быть объяснимым (событие `move_failed` с причиной), а не
молчаливым «ничего не произошло».
"""
import asyncio

import pytest

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_engine.types.conditions import ActiveCondition
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=16, dexterity=14, constitution=14,
    intelligence=10, wisdom=10, charisma=8,
)

SPEED_ZERO = ("grappled", "restrained", "paralyzed", "petrified", "unconscious")
SPEED_KEPT = ("prone", "poisoned")


def _move_attempt(condition):
    """Один ход: персонаж пытается пройти из `0,0` в `2,0`."""
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=1, ability_scores=SCORES,
        selected_choices=("archery", "skilled"),
    )
    fighter = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Fighter", zone_id="0,0", initiative=20,
            hp_max=60, hp_current=60,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="speed",
            party=[fighter],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=100, hp_max=100, ac=12, zone_id="5,5",
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=5,
        )
    ).handle
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == "pc1")
    if condition is not None:
        hero.conditions = [
            ActiveCondition(
                condition=condition, source_entity_id="implied:aura", scope="combat",
            )
        ]
    speed = O._effective_speed(hero)
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1", O.PlayerIntent(intent_type="move", target_zone_id="2,0"),
        )
    )
    events = O.drain_pending_events(handle)
    reasons = [e.reason for e in events if e.type == "move_failed"]
    return speed, O._get_live(handle).actor_zone.get("pc1"), reasons


def test_a_healthy_character_can_move():
    speed, zone, reasons = _move_attempt(None)
    assert speed == 30, "базовая скорость не 30: " + str(speed)
    assert zone == "2,0", "персонаж не переместился: " + str(zone)
    assert reasons == [], "ложный отказ движения: " + str(reasons)


@pytest.mark.parametrize("condition", SPEED_ZERO)
def test_speed_zero_conditions_block_movement_with_a_reason(condition):
    speed, zone, reasons = _move_attempt(condition)
    assert speed == 0, condition + ": скорость не обнулена, " + str(speed)
    assert zone == "0,0", condition + ": персонаж всё равно переместился в " + str(zone)
    assert reasons == ["speed_zero"], (
        condition + ": отказ должен быть объясним, получено " + str(reasons)
    )


@pytest.mark.parametrize("condition", SPEED_KEPT)
def test_conditions_that_do_not_zero_speed_still_allow_movement(condition):
    """Prone ограничивает способ движения, а не скорость; Poisoned скорость не трогает."""
    speed, zone, reasons = _move_attempt(condition)
    assert speed == 30, condition + " не должен обнулять скорость, получено " + str(speed)
    assert zone == "2,0", condition + ": перемещение запрещено напрасно"
    assert reasons == [], condition + ": ложный отказ " + str(reasons)

