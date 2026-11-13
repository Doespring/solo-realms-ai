"""SRD 5.2 §Making an Attack — the target of an attack must exist.

An attack naming an id that is not in the fight used to be accepted: the
engine emitted ``intent_submitted``, looked the id up in the initiative order,
found nobody, resolved nobody, and still spent the Action. The host saw a
successful submission with no ``attack_rolled`` and a turn whose Action had
silently vanished — a legal-looking command with no mechanic behind it.

The reach gate could not catch it: it reads the target cell, and an unknown
target has none, so it reported "in range". The gates now refuse the attack
before anything is spent.
"""

from __future__ import annotations

import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=16, dexterity=14, constitution=16,
    intelligence=8, wisdom=10, charisma=8,
)


def _fight(seed=7):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=5, ability_scores=SCORES,
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
            session_id="target", party=[fighter],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=200, hp_max=200, ac=5,
                    zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=10, height=6), rng_seed=seed,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _snapshot(handle):
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == "pc1")
    return {
        "action": hero.action_available,
        "attacks": hero.attacks_remaining,
        "hp": dict(live.tracked_hp),
    }


def _attack(handle, target):
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(intent_type="attack", target_id=target, weapon_id="greataxe"),
        )
    )
    return O.drain_pending_events(handle)


def test_an_attack_on_an_absent_creature_is_refused():
    """Отказ приходит, а не «успешная отправка» без броска."""
    handle = _fight()
    events = _attack(handle, "no-such-creature")
    kinds = [e.type for e in events]
    assert "intent_submitted" not in kinds, "несбыточная атака подана как успешная: " + str(kinds)
    failed = next((e for e in events if e.type == "attack_failed"), None)
    assert failed is not None, "нет отказа, события: " + str(kinds)
    assert failed.reason == "target_invalid", "причина отказа: " + str(failed.reason)
    assert failed.target_id == "no-such-creature", str(failed.target_id)


def test_the_refused_attack_spends_nothing():
    """Ни действие, ни бюджет атак не тратятся на отказ (§13)."""
    handle = _fight()
    before = _snapshot(handle)
    _attack(handle, "no-such-creature")
    after = _snapshot(handle)
    assert after == before, "отказ изменил состояние: " + str(before) + " -> " + str(after)


def test_a_real_target_still_gets_its_attack_roll():
    """Отрицательный контроль: законная атака не задета."""
    handle = _fight()
    events = _attack(handle, "m1")
    kinds = [e.type for e in events]
    assert "attack_rolled" in kinds, "законная атака не брошена: " + str(kinds)
    assert "attack_failed" not in kinds, str(kinds)
