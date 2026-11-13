"""Uncanny Metabolism (SRD 5.2, монах 2).

«When you roll Initiative, you can regain all expended Focus Points. When you
do so, roll your Martial Arts die, and regain a number of Hit Points equal to
your Monk level plus the number rolled. Once you use this feature, you can't
use it again until you finish a Long Rest.»

Проверки идут через публичный `start_combat` и смотрят на счётчики и HP.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

SCORES = AbilityScores(
    strength=12, dexterity=16, constitution=14,
    intelligence=10, wisdom=14, charisma=8,
)
FOCUS = 'feature_use:monks-focus'
UNCANNY = 'feature_use:uncanny-metabolism'


def _monk(level: int, counters: dict, hp: int = 10) -> object:
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="monk", background_slug="soldier",
        level=level, ability_scores=SCORES,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Monk", zone_id="0,0", initiative=20,
            hp_max=40, hp_current=hp,
        ),
        loader=BundledAssetLoader(),
    )
    return member.model_copy(update={'custom_counters': counters})


def _fight(level: int, counters: dict, hp: int = 10) -> object:
    live = O._get_live(
        asyncio.run(
            O.start_combat(
                session_id="uncanny-metabolism",
                party=[_monk(level, counters, hp)],
                encounter=[
                    EncounterMemberSpec(
                        entity_id="m1", name="Foe", entity_type="Monster",
                        initiative=1, hp_current=9, hp_max=9, ac=10, zone_id='0,1',
                    )
                ],
                grid_scene=GridScene(width=10, height=10),
                rng_seed=5,
            )
        ).handle
    )
    return live


def test_all_focus_returns_and_the_monk_heals() -> None:
    """Фокус возвращается, лечение = уровень монаха + кость боевых искусств."""
    live = _fight(2, {FOCUS: {'spent': 3}, UNCANNY: {'spent': 0}})
    counters = live.custom_counters_by_entity['pc1']
    assert counters[FOCUS]['spent'] == 0, 'весь фокус возвращается'
    assert counters[UNCANNY]['spent'] == 1, 'признак потрачен до длительного отдыха'
    monk = live.initiative[0]
    # Монах 2: d6 → лечение лежит в [2 + 1, 2 + 6] поверх стартовых 10 HP.
    assert 10 + 2 + 1 <= monk.hp_current <= 10 + 2 + 6, monk.hp_current
    assert live.tracked_hp['pc1'] == monk.hp_current


def test_the_feature_is_spent_for_the_rest_of_the_day() -> None:
    """Второе срабатывание в тот же день — no-op."""
    live = _fight(2, {FOCUS: {'spent': 3}, UNCANNY: {'spent': 1}})
    counters = live.custom_counters_by_entity['pc1']
    assert counters[FOCUS]['spent'] == 3
    assert live.initiative[0].hp_current == 10


def test_a_monk_with_focus_to_spare_keeps_the_feature() -> None:
    """Нечего возвращать — признак не тратится (бюджет «раз в отдых» цел)."""
    live = _fight(2, {FOCUS: {'spent': 0}})
    counters = live.custom_counters_by_entity['pc1']
    assert counters.get(UNCANNY, {'spent': 0})['spent'] == 0
    assert live.initiative[0].hp_current == 10


def test_a_monk_below_level_2_is_untouched() -> None:
    """До 2 уровня правила нет."""
    live = _fight(1, {FOCUS: {'spent': 3}})
    counters = live.custom_counters_by_entity['pc1']
    assert counters[FOCUS]['spent'] == 3
    assert live.initiative[0].hp_current == 10
