"""Требование 6/7: снятие состояния повторным спасброском в конце хода.

Hold Person (SRD 5.2): цель, провалившая спасбросок МУД, получает Paralyzed и
повторяет спасбросок «в конце каждого своего хода». Успех снимает состояние,
провал оставляет его до следующего хода.

Проверяется через `live.repeat_save_on_turn_end` и фактическое состояние цели,
а не по коду обработчика: обе ветки (успех/провал) обязаны наблюдаться.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=8, dexterity=14, constitution=14,
    intelligence=17, wisdom=10, charisma=8,
)


def _hold_person_end_turn(seed):
    """Наложить Hold Person и дождаться повторного спасброска в конце хода цели."""
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="sage",
        level=5, ability_scores=SCORES,
    )
    wizard = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Wizard", zone_id="0,0", initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="save",
            party=[wizard],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Target", entity_type="Monster",
                    initiative=1, hp_current=100, hp_max=100, ac=10, zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=seed,
        )
    ).handle
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(
                intent_type="cast_spell", spell_id="hold-person", target_id="m1",
            ),
        )
    )
    O.drain_pending_events(handle)
    for _ in range(4):
        live = O._get_live(handle)
        if live.ended:
            break
        try:
            asyncio.run(
                O.submit_player_intent(
                    handle, live.current_actor_id, O.PlayerIntent(intent_type="pass"),
                )
            )
        except Exception:
            pass
        events = O.drain_pending_events(handle)
        repeat = [
            e for e in events
            if e.type == "save_rolled" and e.target_id == "m1" and e.ability == "wis"
        ]
        if repeat:
            live = O._get_live(handle)
            target = next(c for c in live.initiative if c.entity_id == "m1")
            kinds = [e.type for e in events]
            return (
                repeat[0].succeeded,
                [c.condition for c in target.conditions],
                len(live.repeat_save_on_turn_end),
                kinds,
            )
    return None, None, None, None


def test_a_successful_repeat_save_ends_the_condition():
    ok, conditions, pending, kinds = _hold_person_end_turn(14)
    assert ok is True, "повторный спасбросок не наблюдался"
    assert conditions == [], "состояние осталось после успеха: " + str(conditions)
    assert pending == 0, "запись о повторном спасброске не снята"
    assert "condition_removed" in kinds, "не было события condition_removed"
    assert "effect_expired" in kinds, "не было события effect_expired"


def test_a_failed_repeat_save_keeps_the_condition():
    ok, conditions, pending, _ = _hold_person_end_turn(4)
    assert ok is False, "ожидался провал спасброска"
    assert conditions == ["paralyzed"], (
        "провал не оставил состояние: " + str(conditions)
    )
    assert pending == 1, "запись о повторном спасброске потеряна после провала"

