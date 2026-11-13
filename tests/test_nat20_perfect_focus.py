"""Perfect Focus (SRD 5.2, монах 15).

«When you roll Initiative and don't use Uncanny Metabolism, you regain
expended Focus Points until you have 4 if you have 3 or fewer.»

Два правила — одно решение: либо монах тратит Uncanny Metabolism (полный
возврат плюс лечение), либо получает пол в четыре. Поэтому Perfect Focus
проверяется в случае, когда Uncanny Metabolism на этом броске не применялся.
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


def _live_monk(level: int, counters: dict, session: str = 'perfect-focus') -> object:
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="monk", background_slug="soldier",
        level=level, ability_scores=SCORES,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Monk", zone_id="0,0", initiative=20,
            hp_max=40, hp_current=40,
        ),
        loader=BundledAssetLoader(),
    )
    member = member.model_copy(update={'custom_counters': counters})
    handle = asyncio.run(
        O.start_combat(
            session_id=session,
            party=[member],
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
    O.drain_pending_events(handle)
    return O._get_live(handle)


def test_uncanny_metabolism_wins_the_same_roll() -> None:
    """Если Uncanny Metabolism не потрачен, он забирает возврат целиком."""
    live = _live_monk(15, {FOCUS: {'spent': 13}})
    counters = live.custom_counters_by_entity['pc1']
    assert counters[UNCANNY]['spent'] == 1, 'Uncanny Metabolism израсходован'
    assert counters[FOCUS]['spent'] == 0, 'фокус возвращён полностью'


def test_the_floor_of_four_applies_when_uncanny_was_already_used() -> None:
    """Uncanny уже потрачен сегодня — действует пол в четыре."""
    live = _live_monk(15, {FOCUS: {'spent': 13}, UNCANNY: {'spent': 1}})
    counters = live.custom_counters_by_entity['pc1']
    # Пул на 15 уровне — 15, пол оставляет 4 неистраченных => spent == 11.
    assert counters[FOCUS]['spent'] == 11, counters


def test_a_monk_with_four_left_is_untouched() -> None:
    """Ровно четыре оставшихся — правило не срабатывает."""
    live = _live_monk(15, {FOCUS: {'spent': 11}, UNCANNY: {'spent': 1}})
    assert live.custom_counters_by_entity['pc1'][FOCUS]['spent'] == 11


def test_the_feature_is_absent_before_level_15() -> None:
    """До 15 уровня пола нет."""
    live = _live_monk(14, {FOCUS: {'spent': 13}, UNCANNY: {'spent': 1}})
    assert live.custom_counters_by_entity['pc1'][FOCUS]['spent'] == 13


def test_the_next_fight_starts_without_a_stale_marker() -> None:
    """Метка «Uncanny применён в этом броске» не переживает бой."""
    # Первый бой: Uncanny Metabolism ещё не тратил, он срабатывает.
    first = _live_monk(15, {FOCUS: {'spent': 13}}, session='fight-one')
    assert first.custom_counters_by_entity['pc1'][FOCUS]['spent'] == 0
    # Второй бой: Uncanny уже израсходован на день, значит действует пол в
    # четыре. Протухшая метка первого боя заглушила бы его.
    second = _live_monk(
        15,
        {FOCUS: {'spent': 13}, UNCANNY: {'spent': 1}},
        session='fight-two',
    )
    assert second.custom_counters_by_entity['pc1'][FOCUS]['spent'] == 11, (
        'второй бой обязан применить пол в четыре: '
        + repr(second.custom_counters_by_entity['pc1'])
    )