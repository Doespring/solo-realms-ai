"""Предмет с активностью без механики не должен молча съедать действие (п.7, п.8).

`activities/resolver.py` исполняет `utility` без `effects[]` и
`summon`/`enchant`/`transform` вне allowlist как нарративный passthrough:
пишет `activity_kind_narrative` и возвращает None, не выдавая событий.
Для заклинаний это допустимо, но `use_item` проверял только наличие
предмета — действие списывалось, а игрок видел `intent_submitted` и
тишину. Замерено: `apparatus-of-the-crab` переводил `action_available`
из True в False без единого события механики.

Источник («Move Levers»: «Adjust up to two levers up or down») числа не
даёт, поэтому движок не выдумывает исход, а называет его: `cast_failed`
с причиной `narrative_activity`, ДО списания бюджета.
"""
import asyncio

from dnd5e_engine import (
    GridScene,
    PlayerIntent,
    drain_pending_events,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec
from dnd5e_srd_data.loader import BundledAssetLoader

LEVERS = "apparatus-of-the-crab"
MOVE_LEVERS = "h7YokhkkRF3yKBWn"
BOOTS = "boots-of-speed"
CLICK_HEELS = "ffXywPJdVzwe6NId"
CHIME = "chime-of-opening"
CHIME_KNOCK = "FW2x7qXh6xbtpxRQ"


def _fight(*items):
    loader = BundledAssetLoader()
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="acolyte", level=17,
        ability_scores=AbilityScores(
            strength=16, dexterity=14, constitution=16,
            intelligence=18, wisdom=16, charisma=16,
        ),
    )
    member = build_party_member(
        spec,
        CombatInstance(entity_id="pc1", name="P", zone_id="0,0",
                       initiative=20, hp_max=200, hp_current=100),
        loader=loader,
    )
    member.equipment = list(getattr(member, "equipment", []) or []) + list(items)
    # Требование 5: настройка — законное условие активации. Без неё движок
    # отказывает `not_attuned`, что для предметов с этим свойством верно.
    # `attuned_items` — кортеж: список настройки собирается заново.
    if type(member).model_fields.get("attuned_items") is not None:
        member = member.model_copy(
            update={"attuned_items": tuple(member.attuned_items) + tuple(items)}
        )
    handle = asyncio.run(start_combat(
        session_id="s", party=[member],
        encounter=[EncounterMemberSpec(
            entity_id="m1", name="Target", entity_type="Monster",
            initiative=1, hp_current=999, hp_max=999, ac=12, zone_id="1,0")],
        grid_scene=GridScene(width=10, height=10), rng_seed=5,
    )).handle
    drain_pending_events(handle)
    return handle


def _use(handle, item_id, activity_id=None):
    asyncio.run(submit_player_intent(
        handle, "pc1",
        PlayerIntent(
            intent_type="use_item", item_id=item_id,
            activity_id=activity_id, target_id="m1",
        ),
    ))
    return drain_pending_events(handle)


def test_a_narrative_activity_is_named_not_silent():
    events = _use(_fight(LEVERS), LEVERS, MOVE_LEVERS)
    failures = [e for e in events if e.type == "cast_failed"]
    assert len(failures) == 1, [e.type for e in events]
    assert failures[0].reason == "narrative_activity"


def test_a_narrative_activity_does_not_spend_the_action():
    """Раньше `action_available` переходил True -> False на пустом исходе."""
    handle = _fight(LEVERS)
    before = _action_available(handle)
    _use(handle, LEVERS, MOVE_LEVERS)
    assert before is True
    assert _action_available(handle) is True


def test_no_mechanics_fire_for_a_narrative_activity():
    """Отказ, а не поддельный успех: ни урона, ни эффекта, ни спасброска."""
    events = _use(_fight(LEVERS), LEVERS, MOVE_LEVERS)
    mechanical = {"damage_applied", "healing_applied", "effect_applied",
                  "save_rolled", "attack_rolled", "check_rolled", "summon_created"}
    assert not [e for e in events if e.type in mechanical]


def test_an_item_with_real_mechanics_still_resolves():
    """Гейт не должен глушить предметы, у которых механика есть."""
    events = _use(_fight("potion-of-healing"), "potion-of-healing")
    assert [e.reason for e in events if e.type == "cast_failed"] == []
    assert any(e.type == "healing_applied" for e in events), [e.type for e in events]


def test_a_utility_activity_with_rider_effects_still_resolves():
    """Граница проходит по `effects[]`, а не по строке `utility`.

    `resolver.py` применяет рейдеры `utility`-активности, когда они есть
    (Boots of Speed «Click Heels»). Если объявить нарративным весь вид
    `utility`, эти 46 активностей замолчат — тест это ловит.
    """
    events = _use(_fight(BOOTS), BOOTS, CLICK_HEELS)
    assert [e.reason for e in events if e.type == "cast_failed"] == []
    assert any(e.type == "effect_applied" for e in events), [e.type for e in events]

def test_an_item_that_delegates_to_a_narrative_spell_is_named():
    """Chime of Opening cast-активность ведёт на Knock (utility без effects).

    Механика здесь определяется заклинанием-целью, а не видом обёртки:
    `cast` судится по делегированной активности (resolver.py:66-104).
    Замер до правки: `intent_submitted` и действие списано без событий.
    """
    events = _use(_fight(CHIME), CHIME, CHIME_KNOCK)
    failures = [e for e in events if e.type == "cast_failed"]
    assert [f.reason for f in failures] == ["narrative_activity"], [e.type for e in events]

def test_the_refusal_names_the_item():
    events = _use(_fight(LEVERS), LEVERS, MOVE_LEVERS)
    failure = next(e for e in events if e.type == "cast_failed")
    assert failure.spell_id == LEVERS


def _action_available(handle):
    from dnd5e_engine.orchestrator import _get_live

    live = _get_live(handle)
    return next(c for c in live.initiative if c.entity_id == "pc1").action_available
