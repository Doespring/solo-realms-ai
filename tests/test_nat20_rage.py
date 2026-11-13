"""Требование 7: активация способности обязана давать ЖИВУЮ механику.

ЗАКРЫТЫЙ ДЕФЕКТ (раунды 67–68). Симптом: `use_feature rage` выдавал
`effect_applied`, но урон не менялся ни на одном зерне — событие есть,
механики нет (ровно то, что §7 запрещает считать реализацией).

Причина: шаблон пассивного эффекта в корпусе несёт `disabled: true`
(Foundry: эффект выключен на актёре до включения), а
`activities/effects.py:197` переносил флаг в `ActiveEffect` буквально,
и фильтр `orchestrator.py:3652 _enabled_effects` выбрасывал эффект целиком.

Исправление: эффект, ПОСТРОЕННЫЙ активацией, включён по определению —
`disabled` описывает шаблон в покое (`build_party.py:162-168` трактует его
как «отбросить после переписывания предмета»). Фильтр не тронут.

Проверки идут через публичный бой и смотрят на урон и сопротивления,
а не на факт события.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.activities.apply import _effective_resistances
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=18, dexterity=14, constitution=16,
    intelligence=8, wisdom=10, charisma=8,
)


def _raging_fight(*, rage, seed):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="barbarian", background_slug="soldier",
        level=5, ability_scores=SCORES,
    )
    barbarian = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Barbarian", zone_id="0,0", initiative=20,
            hp_max=60, hp_current=60,
        ),
        loader=BundledAssetLoader(),
    )
    handle = asyncio.run(
        O.start_combat(
            session_id="rage", party=[barbarian],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Dummy", entity_type="Monster",
                    initiative=1, hp_current=400, hp_max=400, ac=1, zone_id="1,0",
                )
            ],
            grid_scene=GridScene(width=8, height=6), rng_seed=seed,
        )
    ).handle
    if rage:
        asyncio.run(
            O.submit_player_intent(
                handle, "pc1",
                O.PlayerIntent(intent_type="use_feature", feature_id="rage"),
            )
        )
        O.drain_pending_events(handle)
    return handle


def _damage(handle):
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1",
            O.PlayerIntent(intent_type="attack", target_id="m1", weapon_id="greatsword"),
        )
    )
    events = O.drain_pending_events(handle)
    hit = next(e for e in events if e.type == "damage_applied")
    return hit.amount


def test_rage_raises_melee_weapon_damage_by_the_rage_bonus():
    """Урон в ярости больше ровно на бонус ярости (Rage Damage)."""
    bonus = None
    for seed in (11, 12, 13):
        calm = _damage(_raging_fight(rage=False, seed=seed))
        hot = _damage(_raging_fight(rage=True, seed=seed))
        delta = hot - calm
        if bonus is None:
            bonus = delta
        assert delta == bonus, (
            f"прирост урона непостоянен: {delta} против {bonus} (seed {seed})"
        )
        assert delta > 0, (
            f"ярость не добавила урона (seed {seed}): " + str(calm) + " -> " + str(hot)
        )
    # Варвар 5 уровня: Rage Damage +2 по таблице класса.
    assert bonus == 2, "бонус урона ярости должен быть +2 на 5 уровне, получено " + str(bonus)


def test_rage_grants_resistance_to_physical_damage():
    """Сопротивления из эффекта ярости доходят до расчёта урона."""
    handle = _raging_fight(rage=True, seed=11)
    live = O._get_live(handle)
    hero = next(c for c in live.initiative if c.entity_id == "pc1")
    sidecar: dict = {}
    O._project_target_modifiers(hero, live, sidecar)
    granted = _effective_resistances(hero, sidecar.get("pc1", {}), magical=False)
    assert "slashing" in granted, "ярость не дала сопротивления рубящему: " + str(granted)
    assert "piercing" in granted, "ярость не дала сопротивления колющему: " + str(granted)
    assert "bludgeoning" in granted, "ярость не дала сопротивления дробящему: " + str(granted)

