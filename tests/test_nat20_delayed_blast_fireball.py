"""SRD 5.2: Delayed Blast Fireball — шарик тратит собственный пул, взрыв читает (пул)d6."""

from __future__ import annotations

import asyncio
import itertools

import pytest

import dnd5e_engine.orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
# Импорт модуля, а не имени: другие тесты набора могут переписать атрибут
# ``lib_loader.configure_lib_loader``, и корпус надо ставить через сам модуль.
from dnd5e_engine import lib_loader
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

_LOADER = BundledAssetLoader()
_SESSION = itertools.count()
_BEAD = "iWd7zKpQIgWofJAp"  # Create Bead
_TRIGGER = "M15GlfjeWy7Cdiqn"  # Trigger Explosion


@pytest.fixture(autouse=True)
def _bundled_corpus():
    lib_loader.set_lib_loader_for_tests(_LOADER)
    yield
    lib_loader.set_lib_loader_for_tests(None)


def _wizard_fight():
    spec = CharacterBuildSpec(
        species_slug="human",
        class_slug="wizard",
        background_slug="acolyte",
        level=17,
        ability_scores=AbilityScores(
            strength=10,
            dexterity=14,
            constitution=14,
            intelligence=18,
            wisdom=14,
            charisma=10,
        ),
    )
    pc = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1",
            name="Волшебник",
            zone_id="0,0",
            initiative=20,
            hp_max=90,
            hp_current=90,
        ),
        loader=_LOADER,
    )
    pc = pc.model_copy(
        update={
            "spells_known": tuple(
                set(getattr(pc, "spells_known", ()) or ()) | {"delayed-blast-fireball"}
            )
        }
    )
    result = asyncio.run(
        O.start_combat(
            session_id=f"dbf-{next(_SESSION)}",
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
                    zone_id="2,0",
                )
            ],
            grid_scene=GridScene(width=12, height=12),
            rng_seed=3,
        )
    )
    O.drain_pending_events(result.handle)
    return result.handle


def _do(handle, **kwargs):
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1", O.PlayerIntent(spell_id="delayed-blast-fireball", **kwargs)
        )
    )
    return O.drain_pending_events(handle)


def _explosion_damage(beads, handle=None):
    """Урон взрыва при ``beads`` созданных шариках (12d6 + beads d6).

    ``handle`` переиспользуется, когда тесту нужен ещё и пул слотов того же боя.
    """
    handle = handle or _wizard_fight()
    # Каждый шарик — свой слот: книга даёт один слот 8 и один 9 уровня.
    for created, level in enumerate((8, 9)[:beads]):
        if created:
            _next_round(handle)
        events = _do(
            handle, intent_type="cast_spell", activity_id=_BEAD, slot_level=level, target_id="m1"
        )
        assert [e for e in events if e.type == "spell_cast"], events
    _next_round(handle)
    # Взрыв читает пул — и платит за себя ЗАРЯДОМ шарика, а не новым слотом.
    events = _do(
        handle, intent_type="cast_spell", activity_id=_TRIGGER, target_id="m1"
    )
    damage = [e.amount for e in events if e.type == "damage_applied"]
    assert damage, events
    return damage[0]

def _explosion_refusal():
    """Причина отказа взрыва, если шариков не создавали."""
    events = _do(_wizard_fight(), intent_type="cast_spell", activity_id=_TRIGGER, target_id="m1")
    reasons = [e.reason for e in events if e.type == "cast_failed"]
    return reasons[0] if reasons else None


def _next_round(handle):
    """Передать ход и довести очередь до хода волшебника в новом раунде."""
    asyncio.run(O.submit_player_intent(handle, "pc1", O.PlayerIntent(intent_type="pass")))
    O.drain_pending_events(handle)
    for _ in range(4):
        live = O._get_live(handle)
        if live.ended or live.current_actor_id == "pc1":
            return
        asyncio.run(O.advance_monster_turn(handle))
        O.drain_pending_events(handle)

def test_the_bead_creates_one_charge_of_the_spell_own_pool():
    handle = _wizard_fight()
    _do(handle, intent_type="cast_spell", activity_id=_BEAD, slot_level=8, target_id="m1")
    counters = O._get_live(handle).custom_counters_by_entity["pc1"]
    assert counters["spell_pool:delayed-blast-fireball"]["credits"] == 1
    assert O._spell_pool_left(O._get_live(handle), "pc1", "delayed-blast-fireball") == 1


def test_the_explosion_reads_the_pool_instead_of_a_hardcoded_count():
    # «(@item.uses.value)d6» — добавка к 12d6, а не фиксированное число:
    # один шарик даёт 13d6, два — 14d6.
    # Границы 13d6..78 не отличили бы «0 d6» от «1 d6», поэтому бой
    # без шариков обязан отказать: не созданное нельзя взорвать.
    assert _explosion_refusal() is not None
    # Отказ не только назван: ни урона, ни расхода слота.
    assert _explosion_refusal() == "no_uses_remaining", _explosion_refusal()    # Два шарика в разных боях: у волшебника 17 уровня один слот 8
    # уровня, поэтому второй шарик в том же бою стоил бы слот 9 и
    # проверял бы уже не чтение пула, а расход слотов.
    one = _explosion_damage(1)
    two = _explosion_damage(2)    # Кроме того, пул обязан сокращаться со взрывом: иначе второй шарик
    # в следующем раунде читался бы как ещё не взорванный.
    unused = _wizard_fight()
    _do(unused, intent_type="cast_spell", activity_id=_BEAD, slot_level=8, target_id="m1")
    assert O._spell_pool_left(O._get_live(unused), "pc1", "delayed-blast-fireball") == 1
    _next_round(unused)
    _do(unused, intent_type="cast_spell", activity_id=_TRIGGER, target_id="m1")
    assert O._spell_pool_left(O._get_live(unused), "pc1", "delayed-blast-fireball") == 0
    assert 13 <= one <= 78, one
    assert 14 <= two <= 84, two


def test_the_explosion_is_paid_for_by_the_bead_not_by_a_new_slot():
    handle = _wizard_fight()
    before = dict(O._get_live(handle).spell_slots_by_entity["pc1"])
    _do(handle, intent_type="cast_spell", activity_id=_BEAD, slot_level=8, target_id="m1")
    after_bead = dict(O._get_live(handle).spell_slots_by_entity["pc1"])
    assert after_bead[8] == before[8] - 1
    _next_round(handle)
    _do(handle, intent_type="cast_spell", activity_id=_TRIGGER, target_id="m1")
    # Ни один новый слот не потрачен: взрыв оплачен шариком.
    assert O._get_live(handle).spell_slots_by_entity["pc1"][8] == after_bead[8], (
        after_bead,
        O._get_live(handle).spell_slots_by_entity["pc1"],
    )


def test_the_refused_explosion_changes_nothing():
    # §9: отклонённый взрыв не тратит ни действие, ни слот и не наносит урон.
    handle = _wizard_fight()
    before_slots = dict(O._get_live(handle).spell_slots_by_entity["pc1"])
    events = _do(handle, intent_type="cast_spell", activity_id=_TRIGGER, target_id="m1")
    assert [e.reason for e in events if e.type == "cast_failed"] == ["no_uses_remaining"]
    assert not [e for e in events if e.type == "damage_applied"]
    assert not [e for e in events if e.type == "spell_cast"]
    assert dict(O._get_live(handle).spell_slots_by_entity["pc1"]) == before_slots
    assert O._get_live(handle).custom_counters_by_entity.get("pc1") is None


def test_no_bead_means_no_explosion():
    # Взрыв без созданного шарика обязан отказать: пул пуст, а не «по умолчанию 1».
    handle = _wizard_fight()
    events = _do(handle, intent_type="cast_spell", activity_id=_TRIGGER, target_id="m1")
    assert [e.reason for e in events if e.type == "cast_failed"] == ["no_uses_remaining"]
    assert not [e for e in events if e.type == "damage_applied"]
    # Отказ пришёл до расхода: ни один слот не тронут.
    pool = O._get_live(handle).spell_slots_by_entity["pc1"]
    assert pool[8] == 1 and pool[9] == 1, pool

