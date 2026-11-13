"""Требование 6 и 7: способность меняет СОСТОЯНИЕ, а не только статус вызова.

Здесь зафиксирован урок: первая версия этого теста искала событие-эффект
(`healing_applied` и подобное) и падала на `action-surge` и `indomitable`.
Обе способности действительно работают, но меняют ПОЛЕ СОСТОЯНИЯ, не выдавая
отдельного события. Поэтому проверяется состояние ДО и ПОСЛЕ — прямое указание
п.7: «Проверь фактическое изменение состояния и результата расчёта».

Источник параметров — `canonical/classes/fighter.json`:
- `Action Surge` — `uses.max: @scale.fighter.action-surge`, `Trait` на 2 ур.;
- `Indomitable` — `uses.max: @scale.fighter.indomitable`, `scale {9:1, 13:2, 17:3}`;
  `Trait` на 9 ур.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=16, dexterity=12, constitution=14,
    intelligence=10, wisdom=10, charisma=8,
)


def _handle(level):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", background_slug="soldier",
        level=level, ability_scores=SCORES,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Fighter", zone_id="0,0", initiative=20,
            hp_max=200, hp_current=200,
        ),
        loader=BundledAssetLoader(),
    )
    return asyncio.run(
        O.start_combat(
            session_id="features",
            party=[member],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=99, hp_max=99, ac=10, zone_id="2,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6),
            rng_seed=4,
        )
    ).handle


def _combatant(handle):
    live = O._get_live(handle)
    return live, next(c for c in live.initiative if c.entity_id == "pc1")


def _use(handle, feature_id):
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(intent_type="use_feature", feature_id=feature_id),
        )
    )
    O.drain_pending_events(handle)


def test_action_surge_grants_an_extra_action():
    """SRD 5.2 «take one additional action, except the Magic action»."""
    handle = _handle(9)
    _, fighter = _combatant(handle)
    assert fighter.extra_actions_remaining == 0
    _use(handle, "action-surge")
    _, fighter = _combatant(handle)
    assert fighter.extra_actions_remaining == 1, "дополнительное действие не выдано"
    assert fighter.action_surge_used_this_turn is True


def test_action_surge_is_once_per_turn():
    """SRD 5.2: «but only once on a turn» — второй за ход отвергается."""
    handle = _handle(17)
    _use(handle, "action-surge")
    _, fighter = _combatant(handle)
    assert fighter.extra_actions_remaining == 1
    _use(handle, "action-surge")
    _, fighter = _combatant(handle)
    assert fighter.extra_actions_remaining == 1, "второе применение за ход не отвергнуто"


def test_indomitable_spends_its_use():
    """SRD 5.2 Indomitable: боец 9 — одно применение до отдыха."""
    handle = _handle(9)
    live, _ = _combatant(handle)
    assert O._feature_use_spent(live, "pc1", "indomitable") == 0
    _use(handle, "indomitable")
    live, _ = _combatant(handle)
    assert O._feature_use_spent(live, "pc1", "indomitable") == 1, (
        "применение Indomitable не учтено — способность без ресурса"
    )


def test_indomitable_is_capped_at_one_before_level_thirteen():
    """`scale {9: 1, 13: 2, 17: 3}` — на 9 уровне второе применение отказ."""
    handle = _handle(9)
    _use(handle, "indomitable")
    _, fighter = _combatant(handle)
    # Второе применение упирается в квоту 1 или в экономику действия хода —
    # обе причины законны; важен отказ, а не тишина.
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(intent_type="use_feature", feature_id="indomitable"),
        )
    )
    events = O.drain_pending_events(handle)
    refusals = {"cast_failed"} & {e.type for e in events}
    live, _ = _combatant(handle)
    spent = O._feature_use_spent(live, "pc1", "indomitable")
    assert refusals or spent == 1, (
        "второе Indomitable не отвергнуто явно и не упёрлось в квоту: "
        f"события {sorted(e.type for e in events)}, списано {spent}"
    )
    assert spent == 1, "квота 1 на 9 уровне превышена"

