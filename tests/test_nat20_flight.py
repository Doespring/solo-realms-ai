"""Требование 6 (перемещение) и 7 (реализация, а не событие).

ЗАКРЫТЫЙ ДЕФЕКТ (раунд 69). Симптом: Dragon Wings (волшебник, Draconic
на 14 уровне) выдавал `effect_applied`, в бою появлялся эффект `Winged`,
но `Combatant.movement_modes.fly` оставался `None` — полёт не работал,
движение на 60 футов отклонялось как `insufficient_movement`.

Причина архитектурная: режимы движения заполняются ОДИН РАЗ при сборке
персонажа (build_spec -> build_party -> orchestrator:7703), тогда как
обычная скорость уже умела пересчитываться из `live.active_effects`
(`_effective_speed`). Эффект, включённый ПОСРЕДИ боя, в режимы не попадал.

Исправление: `_effect_movement_modes` проецирует активные эффекты на
режимы с той же Foundry-семантикой (mode 4 = max, 2 = сложение,
1 = умножение, которое ничего не выдаёт при отсутствии базы);
`_effective_speed` берёт лучший доступный режим; `_grant_movement_headroom`
поднимает неистраченный запас хода, когда эффект включил более быстрый
режим (только скорость, не потраченное: 20 из 30 -> 60, а не 90).

Проверки идут через публичный бой и смотрят на фактическое перемещение,
а не на факт события.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=8, dexterity=14, constitution=14,
    intelligence=10, wisdom=10, charisma=18,
)

WINGS_ACTIVITY = "W3TaYUpTcgqAtLD7"



def _fight(*, wings, seed=1, monster_zone="15,5"):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="sorcerer", subclass_slug="draconic",
        background_slug="acolyte", level=14, ability_scores=SCORES,
    )
    sorcerer = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Sorcerer", zone_id="0,0", initiative=20,
            hp_max=100, hp_current=100,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="wings", party=[sorcerer],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=80, hp_max=80, ac=12,
                    zone_id=monster_zone,
                )
            ],
            grid_scene=GridScene(width=16, height=6), rng_seed=seed,
        )
    ).handle
    O.drain_pending_events(handle)
    if wings:
        asyncio.run(
            O.submit_player_intent(
                handle, "pc1",
                O.PlayerIntent(
                    intent_type="use_feature", feature_id="dragon-wings",
                    activity_id=WINGS_ACTIVITY,
                ),
            )
        )
        O.drain_pending_events(handle)
    return handle


def _move(handle, target):
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1", O.PlayerIntent(intent_type="move", target_zone_id=target),
        )
    )
    events = O.drain_pending_events(handle)
    moved = next((e for e in events if e.type == "actor_moved"), None)
    failed = next((e for e in events if e.type == "move_failed"), None)
    zone = O._get_live(handle).actor_zone.get("pc1")
    if moved is not None:
        return ("moved", None, zone)
    return ("failed", getattr(failed, "reason", None), zone)


def _hero(handle):
    live = O._get_live(handle)
    return live, next(c for c in live.initiative if c.entity_id == "pc1")


def _activate_wings(handle):
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(
                intent_type="use_feature", feature_id="dragon-wings",
                activity_id=WINGS_ACTIVITY,
            ),
        )
    )
    O.drain_pending_events(handle)


def test_dragon_wings_turn_on_a_fly_speed_on_the_combatant():
    """Эффект крыльев доходит до расчёта, а не остаётся событием.

    `Combatant.movement_modes` — исходное значение с листа, записанное при
    сборке; проекция живых эффектов считается на ходу, поэтому проверяется
    через `_effect_movement_modes` и `_effective_speed` (то, что читает
    передвижение).
    """
    handle = _fight(wings=True)
    live, hero = _hero(handle)
    assert hero.movement_modes.fly is None, (
        "полёт не должен появляться с листа — крылья включаются в бою: "
        + str(hero.movement_modes)
    )
    projected = O._effect_movement_modes(hero, live)
    assert projected.fly == 60, "Dragon Wings не дали полёт 60: " + str(projected)
    assert O._effective_speed(hero, live) == 60, (
        "эффективная скорость не учла полёт: " + str(O._effective_speed(hero, live))
    )

def test_the_wings_are_needed_for_the_long_move():
    """Отрицательный случай: без крыльев 60 футов недостижимы, с ними — да."""
    without = _move(_fight(wings=False), "12,0")
    assert without[0] == "failed", "без крыльев дальний ход прошёл: " + str(without)
    assert without[1] == "insufficient_movement", str(without)
    assert without[2] == "0,0", "отказанный ход сдвинул персонажа: " + str(without)

    with_wings = _move(_fight(wings=True), "12,0")
    assert with_wings[0] == "moved", "с крыльями дальний ход отклонён: " + str(with_wings)
    assert with_wings[2] == "12,0", "крылья не довели персонажа: " + str(with_wings)



def test_the_headroom_is_the_new_speed_not_a_second_helping():
    """Запас поднимается до новой скорости, а не прибавляется к остатку."""
    handle = _fight(wings=False)
    _, hero = _hero(handle)
    assert hero.movement_remaining == 30, "запас на старте не равен скорости 30"
    first = _move(handle, "4,0")
    assert first[0] == "moved", first
    _, hero = _hero(handle)
    assert hero.movement_remaining == 10, "пеший расход неверен: " + str(hero.movement_remaining)
    _activate_wings(handle)
    _, hero = _hero(handle)
    assert hero.movement_remaining == 60, "запас после крыльев: " + str(hero.movement_remaining)
