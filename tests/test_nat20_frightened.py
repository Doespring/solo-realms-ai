"""Требование 6/7: испуг (Frightened) — помеха действует ТОЛЬКО пока источник
страха в линии видимости.

SRD 5.2 Frightened: «You have Disadvantage on ability checks and attack rolls
while the source of fear is within line of sight».

Проверяется через обработчик существующего боя: `_fear_source_in_sight` — тот
самый флаг, который читают `conditions_grant_disadvantage_on_*`.
Отрицательный случай — источник за стеной: помеха обязана СНЯТЬСЯ.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.rules.conditions import conditions_grant_disadvantage_on_ability_checks
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_engine.types.conditions import ActiveCondition
from dnd5e_engine.types.effects import ActiveEffect
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=16, dexterity=14, constitution=14,
    intelligence=10, wisdom=10, charisma=8,
)


def _frightened_fight(monster_zone, blocked):
    """Боец испуган монстром; источник страха узнаётся через origin эффекта."""
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
            session_id="fear",
            party=[fighter],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Terrifying", entity_type="Monster",
                    initiative=1, hp_current=100, hp_max=100, ac=12,
                    zone_id=monster_zone,
                )
            ],
            grid_scene=GridScene(width=8, height=6, blocked_cells=blocked),
            rng_seed=5,
        )
    ).handle
    live = O._get_live(handle)
    live.active_effects["pc1"] = [
        ActiveEffect(id="fearfx", name="Fear", origin="cast:fear:m1", target_id="pc1")
    ]
    victim = next(c for c in live.initiative if c.entity_id == "pc1")
    victim.conditions = [
        ActiveCondition(
            condition="frightened", source_entity_id="implied:aura",
            scope="combat", source_effect_id="fearfx",
        )
    ]
    return live, victim


WALL = tuple("1,%d" % row for row in range(6))


def _disadvantage(live, victim):
    seen = O._fear_source_in_sight(live, victim)
    names = O._condition_names(victim)
    return seen, conditions_grant_disadvantage_on_ability_checks(
        names, fear_source_in_sight=seen
    )


def test_frightened_source_in_sight_causes_disadvantage():
    live, victim = _frightened_fight("1,0", ())
    seen, penalty = _disadvantage(live, victim)
    assert seen is True, "источник в открытой местности должен быть в виду"
    assert penalty is True, "испуг без помехи на проверки"


def test_out_of_sight_the_disadvantage_lifts():
    """Источник за стеной: помеха снимается, состояние при этом остаётся."""
    live, victim = _frightened_fight("3,0", WALL)
    monster = next(c for c in live.initiative if c.entity_id == "m1")
    assert O._combatant_can_see(live, victim, monster) is False, (
        "стена не перекрыла линию видимости — сценарий недействителен"
    )
    seen, penalty = _disadvantage(live, victim)
    assert seen is False, "источник за стеной считается видимым"
    assert penalty is False, "помеха осталась при источнике вне вида"
    assert "frightened" in O._condition_names(victim), (
        "само состояние испуга не должно сниматься выходом из вида"
    )


def test_unknown_source_keeps_the_penalty():
    """Если источник не установлен, движок оставляет помеху (консервативно)."""
    live, victim = _frightened_fight("1,0", ())
    live.active_effects["pc1"] = []  # источник больше не восстанавливается
    victim = next(c for c in live.initiative if c.entity_id == "pc1")
    victim.conditions = [
        ActiveCondition(
            condition="frightened", source_entity_id="implied:aura", scope="combat",
        )
    ]
    seen, penalty = _disadvantage(live, victim)
    assert seen is True, "неизвестный источник не должен снимать помеху"
    assert penalty is True

