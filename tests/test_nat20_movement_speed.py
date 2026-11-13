"""SRD 5.2 §Movement при игре: скорости, которые включает само заклинание.

Каждое утверждение проверяется через публичный вход (``submit_player_intent``)
и состояние боя, а не по тексту кода (§7, §13).
"""

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


def _fight(*, klass="wizard", level=5, species="human", scores=None, enemy_zone="8,8"):
    handle = asyncio.run(_start(klass, level, species, scores, enemy_zone))
    O.drain_pending_events(handle)
    return handle


async def _start(klass, level, species, scores, enemy_zone):
    build = CharacterBuildSpec(
        species_slug=species,
        class_slug=klass,
        background_slug="acolyte",
        level=level,
        ability_scores=scores
        or AbilityScores(
            strength=14,
            dexterity=16,
            constitution=14,
            intelligence=18,
            wisdom=12,
            charisma=10,
        ),
    )
    pc = build_party_member(
        build,
        CombatInstance(
            entity_id="pc1",
            name="Герой",
            zone_id="0,0",
            initiative=20,
            hp_max=200,
            hp_current=200,
        ),
        loader=_LOADER,
    )
    result = await O.start_combat(
        session_id=f"move-{next(_SESSION)}",
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
                zone_id=enemy_zone,
            )
        ],
        grid_scene=GridScene(width=12, height=12),
        rng_seed=7,
    )
    return result.handle


def _pc(handle):
    return next(c for c in O._get_live(handle).initiative if c.entity_id == "pc1")


def _speed(handle):
    return O._effective_speed(_pc(handle), O._get_live(handle))


def _cast(handle, spell_id, **extra):
    asyncio.run(
        O.submit_player_intent(
            handle,
            "pc1",
            O.PlayerIntent(
                intent_type="cast_spell", spell_id=spell_id, target_id="pc1", **extra
            ),
        )
    )
    return [
        getattr(ev, "type", type(ev).__name__) for ev in O.drain_pending_events(handle)
    ]


def test_a_spell_granted_climb_speed_reads_the_casters_own_speed():
    # Spider Climb: "The target also gains a Climb Speed equal to its Speed."
    # До правки скорость не появлялась вовсе: ссылка на "вашу Скорость"
    # не разбиралась, и заклинание применялось без механики (§7).
    handle = _fight()
    assert _pc(handle).movement_modes.climb is None

    events = _cast(handle, "spider-climb")

    assert "effect_applied" in events
    modes = O._effect_movement_modes(_pc(handle), O._get_live(handle))
    assert modes.climb == _speed(handle) == 30


def test_the_rider_carries_a_number_not_the_token():
    # Эффект, который ляжет в состояние боя, обязан нести число: токен в
    # нём позже был бы прочитан как «нет скорости» и не отменялся бы
    # повторным применением.
    handle = _fight()
    _cast(handle, "spider-climb")

    effect = O._get_live(handle).active_effects["pc1"][0]

    (change,) = [c for c in effect.changes if c.key.endswith("movement.climb")]
    assert change.mode == "upgrade"
    assert change.value == "30"


def test_a_swim_speed_equal_to_speed_is_a_swim_speed():
    # Alter Self, Aquatic Adaptation: "you gain a Swim Speed equal to your
    # Speed". Источник записывает его значением "1" (эквивалент ходьбы).
    handle = _fight()
    assert _pc(handle).movement_modes.swim is None

    events = _cast(handle, "alter-self", activity_id="dnd5eactivity000")

    assert "effect_applied" in events
    modes = O._effect_movement_modes(_pc(handle), O._get_live(handle))
    assert modes.swim == _speed(handle)


def test_movement_still_costs_the_regular_budget_after_the_spell():
    # Коридор: скорость, которую даёт заклинание, не отменяет цену шага.
    handle = _fight()
    _cast(handle, "spider-climb")
    before = _pc(handle).movement_remaining

    asyncio.run(
        O.submit_player_intent(
            handle, "pc1", O.PlayerIntent(intent_type="move", target_zone_id="2,0")
        )
    )
    moved = [
        getattr(ev, "type", type(ev).__name__) for ev in O.drain_pending_events(handle)
    ]
    assert moved == ["actor_moved"]
    assert _pc(handle).movement_remaining == before - 10


def test_the_change_that_lands_carries_the_number_not_the_token():
    # Эффект, который ляжет в состояние боя, обязан нести расстояние:
    # токен в нём позже был бы прочитан как «нет скорости».
    from dnd5e_engine.orchestrator import _upcoming_movement_changes

    spider_climb = _LOADER.get_spell("spider-climb")
    (effect,) = spider_climb.passive_effects

    (change,) = _upcoming_movement_changes([effect], 30)

    assert change.key == "system.attributes.movement.climb"
    assert change.mode == "upgrade"
    assert change.value == "30"


def test_the_reference_follows_the_speed_the_creature_actually_has():
    # «равна вашей Скорости» — не константа: тот же эффект, колдующий
    # с базой 45 футов (Roving / Fast Movement), даёт 45.
    from dnd5e_engine.orchestrator import _upcoming_movement_changes

    spider_climb = _LOADER.get_spell("spider-climb")
    (effect,) = spider_climb.passive_effects

    (change,) = _upcoming_movement_changes([effect], 45)

    assert change.value == "45"
