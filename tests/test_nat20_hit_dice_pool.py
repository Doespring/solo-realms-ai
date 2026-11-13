"""SRD 5.2: потраченные кости хитов — состояние героя, а не боя."""

from __future__ import annotations

import asyncio
import itertools

import pytest

import dnd5e_engine.orchestrator as O
from dnd5e_engine import lib_loader
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

_LOADER = BundledAssetLoader()
_SESSION = itertools.count()


@pytest.fixture(autouse=True)
def _bundled_corpus():
    lib_loader.set_lib_loader_for_tests(_LOADER)
    yield
    lib_loader.set_lib_loader_for_tests(None)


def _fight(*, level: int, spent: int | None, hp_current: int = 10):
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="wizard",
        background_slug="acolyte",
        level=level,
        ability_scores=AbilityScores(
            strength=10, dexterity=14, constitution=14,
            intelligence=18, wisdom=12, charisma=10,
        ),
    )
    pc = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Волшебник",
            zone_id="0,0",
            initiative=20,
            hp_max=40,
            hp_current=hp_current,
        ),
        loader=_LOADER,
    )
    update: dict = {
        "spells_known": tuple(set(getattr(pc, "spells_known", ()) or ()) | {"arcane-vigor"})
    }
    if spent is not None:
        update["custom_counters"] = {"hit_dice_spent": {"spent": spent}}
    pc = pc.model_copy(update=update)
    handle = asyncio.run(
        O.start_combat(
            session_id=f"hit-dice-{next(_SESSION)}",
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
                    zone_id="4,0",
                )
            ],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=3,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _caster(handle):
    live = O._get_live(handle)
    return live, next(c for c in live.initiative if c.entity_id == "pc1")


def _cast_arcane_vigor(handle):
    spell = _LOADER.get_spell("arcane-vigor")
    heal_activity = next(a for a in spell.activities if a.kind == "heal")
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(
                intent_type="cast_spell",
                spell_id="arcane-vigor",
                activity_id=heal_activity.id,
                slot_level=1,
                target_id="pc1",
            ),
        )
    )
    return O.drain_pending_events(handle)


def test_a_pool_without_a_counter_starts_full():
    handle = _fight(level=5, spent=None)
    _live, caster = _caster(handle)
    assert caster.hit_dice_remaining == {6: 5}


def test_the_counter_the_host_reports_drains_the_pool_before_the_fight():
    handle = _fight(level=5, spent=2)
    live, caster = _caster(handle)
    assert caster.hit_dice_remaining == {6: 3}, caster.hit_dice_remaining
    assert caster.hit_dice_total == {6: 5}


def test_the_pool_of_two_cannot_pay_arcane_vigors_two_dice():
    # Arcane Vigor тратит ДВЕ кости (consumption.targets: hitDice 2). Пул из
    # двух костей покрывает стоимость ровно — но списание идёт ПОСЛЕ лечения,
    # и такой каст оставил бы отказ с уже выданным лечением (§9, половина
    # действия). Поэтому гейт требует, чтобы пул пережил списание, и отказ
    # приходит до любых изменений.
    handle = _fight(level=2, spent=0)
    _live, caster = _caster(handle)
    assert caster.hit_dice_remaining == {6: 2}, caster.hit_dice_remaining
    events = _cast_arcane_vigor(handle)
    failed = [e for e in events if e.type == "cast_failed"]
    assert failed, [e.type for e in events]
    assert failed[0].reason == "no_uses_remaining"
    assert not [e for e in events if e.type == "healing_applied"]
    _live, caster = _caster(handle)
    assert caster.hit_dice_remaining == {6: 2}


def test_a_stale_counter_cannot_invent_dice_the_pool_does_not_hold():
    # Хост может прислать счётчик из прошлой версии героя. Пул — владелец
    # своего размера: счётчик, который «тратит» больше, чем есть, отбрасывается
    # с предупреждением, а не превращает 5 костей в 0.
    handle = _fight(level=5, spent=9)
    live, caster = _caster(handle)
    assert caster.hit_dice_remaining == {6: 5}, caster.hit_dice_remaining
    assert "hit_dice_spent" not in live.custom_counters_by_entity.get("pc1", {})


def test_the_gate_and_the_pool_read_the_same_number():
    # §7: не «обработчик вернул успех», а реальное состояние. Пул на уровне 5
    # равен пяти, Arcane Vigor берёт две — на старте четыре, после каста две.
    handle = _fight(level=5, spent=1)
    _live, caster = _caster(handle)
    assert caster.hit_dice_remaining == {6: 4}, caster.hit_dice_remaining
    events = _cast_arcane_vigor(handle)
    assert not [e for e in events if e.type == "cast_failed"], [
        (e.type, getattr(e, "reason", None)) for e in events
    ]
    healed = [e for e in events if e.type == "healing_applied"]
    assert healed, [e.type for e in events]
    live, caster = _caster(handle)
    assert caster.hit_dice_remaining == {6: 2}, caster.hit_dice_remaining
    counter = live.custom_counters_by_entity["pc1"]["hit_dice_spent"]
    assert counter["spent"] == 3
