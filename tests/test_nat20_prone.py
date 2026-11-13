"""Требование 6: состояние «лежит» (Prone), его снятие и цена подъёма.

SRD 5.2 Prone: «You can spend half your movement (rounded down) to stand up,
ending the condition». Движение подъёма = половина Скорости.

Отрицательный случай проверяется тоже: без достаточного движения подъём
ОТКЛОНЯЕТСЯ, состояние и движение не меняются (п.5 — конкретный объяснимый отказ).
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
PRONE = ActiveCondition(
    condition="prone", source_entity_id="implied:shove", scope="combat",
)


def _prone_fighter(movement):
    """Боец 1 уровня, лежащий, с заданным остатком движения."""
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
            session_id="prone",
            party=[fighter],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=100, hp_max=100, ac=12, zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=5,
        )
    ).handle
    for combatant in O._get_live(handle).initiative:
        if combatant.entity_id == "pc1":
            combatant.conditions = [PRONE]
            combatant.movement_remaining = movement
    return handle


def _pc(handle):
    return next(
        combatant for combatant in O._get_live(handle).initiative
        if combatant.entity_id == "pc1"
    )


def _is_prone(handle):
    return "prone" in [condition.condition for condition in _pc(handle).conditions]


def test_standing_up_costs_half_the_speed_and_ends_prone():
    """Скорость 30 -> подъём стоит 15, состояние снято."""
    handle = _prone_fighter(movement=30)
    assert _is_prone(handle), "исходное состояние не задано"
    asyncio.run(
        O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="stand_up"))
    )
    kinds = {event.type for event in O.drain_pending_events(handle)}
    assert not _is_prone(handle), "подъём не снял состояние prone"
    assert _pc(handle).movement_remaining == 15, (
        "движение после подъёма " + str(_pc(handle).movement_remaining) + ", ожидалось 15"
    )
    assert "condition_removed" in kinds, "не было события condition_removed"


@pytest.mark.parametrize("movement", [0, 10, 14])
def test_standing_up_without_half_movement_is_refused(movement):
    """Меньше половины Скорости — отказ, состояние и движение целы (п.5)."""
    handle = _prone_fighter(movement=movement)
    with pytest.raises(Exception) as raised:
        asyncio.run(
            O.submit_player_intent(
                handle, "pc1", O.PlayerIntent(intent_type="stand_up")
            )
        )
    assert "insufficient_movement" in str(raised.value), (
        "отказ не объяснён нехваткой движения: " + str(raised.value)
    )
    assert _is_prone(handle), "отказ не должен был снять состояние"
    assert _pc(handle).movement_remaining == movement, "отказ изменил движение"


def test_the_exact_half_is_enough():
    """Ровно половина Скорости (15) — подъём разрешён."""
    handle = _prone_fighter(movement=15)
    asyncio.run(
        O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="stand_up"))
    )
    O.drain_pending_events(handle)
    assert not _is_prone(handle), "ровно половины скорости не хватило"
    assert _pc(handle).movement_remaining == 0, "остаток движения не 0"

