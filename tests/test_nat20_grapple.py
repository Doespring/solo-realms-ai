"""Требование 6/7: захват (Grappled) — скорость 0, помеха атакам по всем,
кроме захватчика, и узнаваемый источник. Проверка боем через интенты.

SRD 5.2 `canonical/conditions/grappled.json`, строка 2:
«Speed 0. Your Speed is 0 and can't increase.
 Attacks Affected. You have Disadvantage on attack rolls against any target
 other than the grappler.
 Movable. The grappler can drag or carry you when it moves...»

Все три эффекта объявлены в корпусе как `kinds`: `speed_zero`,
`disadvantage_attacks_except_grappler`, `movable_by_grappler`.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.rules.conditions import conditions_grant_advantage_on_attack
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=18, dexterity=14, constitution=14,
    intelligence=10, wisdom=10, charisma=8,
)


def _grapple_fight(seed=1, monster_zone="1,0"):
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
            session_id="grapple",
            party=[fighter],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Grabbed", entity_type="Monster",
                    initiative=1, hp_current=100, hp_max=100, ac=12,
                    zone_id=monster_zone,
                )
            ],
            grid_scene=GridScene(width=10, height=6),
            rng_seed=seed,
        )
    ).handle
    asyncio.run(
        O.submit_player_intent(
            handle, "pc1", O.PlayerIntent(intent_type="grapple", target_id="m1"),
        )
    )
    events = O.drain_pending_events(handle)
    return handle, events


def _conditions(handle, entity_id):
    live = O._get_live(handle)
    combatant = next(c for c in live.initiative if c.entity_id == entity_id)
    return live, combatant, [ac.condition for ac in combatant.conditions]


def test_grapple_applies_the_condition_through_the_turn():
    handle, events = _grapple_fight()
    kinds = {event.type for event in events}
    assert "save_rolled" in kinds, "захват не потребовал спасброска: " + str(sorted(kinds))
    assert "condition_applied" in kinds, "состояние не наложено: " + str(sorted(kinds))
    live, victim, names = _conditions(handle, "m1")
    assert "grappled" in names, "у цели нет grappled: " + str(names)
    _, grappler, grappler_names = _conditions(handle, "pc1")
    assert "grappled" not in grappler_names, "захватчик сам попал в состояние"
    assert O._condition_source_entity(live, victim, "grappled") == "pc1", (
        "источник захвата не распознан как pc1"
    )


def test_the_grappled_target_has_speed_zero_but_the_grappler_does_not():
    handle, _ = _grapple_fight()
    _, victim, _ = _conditions(handle, "m1")
    _, grappler, _ = _conditions(handle, "pc1")
    assert O._effective_speed(victim) == 0, "захваченный сохранил скорость"
    assert O._effective_speed(grappler) == 30, (
        "захватчик потерял скорость: " + str(O._effective_speed(grappler))
    )


def test_attacks_against_anyone_but_the_grappler_are_at_disadvantage():
    """SRD: «Disadvantage on attack rolls against any target other than the grappler»."""
    handle, _ = _grapple_fight()
    _, victim, names = _conditions(handle, "m1")
    other_adv, other_dis = conditions_grant_advantage_on_attack(
        names, [], grappler_id="pc1", target_id="m2",
    )
    assert other_dis is True, "нет помехи по посторонней цели"
    assert other_adv is False
    own_adv, own_dis = conditions_grant_advantage_on_attack(
        names, [], grappler_id="pc1", target_id="pc1",
    )
    assert own_dis is False, "помеха осталась при атаке по захватчику"
    assert own_adv is False
    free_adv, free_dis = conditions_grant_advantage_on_attack(
        [], [], grappler_id=None, target_id="m2",
    )
    assert (free_adv, free_dis) == (False, False), "ложная помеха у свободного существа"


def test_a_grapple_needs_the_target_within_reach():
    """Захват вне 5 футов отвергается с объяснимой причиной."""
    with_reach = _grapple_fight()[0]
    _, _, names = _conditions(with_reach, "m1")
    assert "grappled" in names, "ближний захват не состоялся"
    from dnd5e_engine.orchestrator import IntentRejectedError

    with pytest_raises(IntentRejectedError) as caught:
        _grapple_fight(monster_zone="4,0")
    assert "out_of_range" in str(caught.value), "неверная причина отказа: " + str(caught.value)


class pytest_raises:
    """Минимальный контекстный менеджер вместо pytest.raises (без зависимости)."""

    def __init__(self, exc_type):
        self.exc_type = exc_type
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError("ожидалось исключение " + self.exc_type.__name__)
        if not issubclass(exc_type, self.exc_type):
            return False
        self.value = exc
        return True

