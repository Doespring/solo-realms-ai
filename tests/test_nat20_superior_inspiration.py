"""Superior Inspiration (SRD 5.2, бард 18): пол применений при инициативе.

Правило: «When you roll Initiative, you regain expended uses of Bardic
Inspiration until you have two if you have fewer than that».

Это ПОЛ, а не полное восстановление: у барда с двумя применениями из трёх
ничего не меняется, у барда без применений остаётся два.
"""
import asyncio

from dnd5e_engine import orchestrator as O
from dnd5e_engine.build_party import build_party_member
from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, CombatInstance
from dnd5e_engine.specs import EncounterMemberSpec, GridScene
from dnd5e_srd_data.loader import BundledAssetLoader

# Charisma 16 → +3 → ``max(1, @abilities.cha.mod)`` = 3 применения.
SCORES = AbilityScores(
    strength=8, dexterity=14, constitution=12,
    intelligence=10, wisdom=10, charisma=16,
)
KEY = 'feature_use:bardic-inspiration'


def _bard(level: int, spent: int) -> object:
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="bard", background_slug="soldier",
        level=level, ability_scores=SCORES,
    )
    member = build_party_member(
        spec,
        CombatInstance(
            entity_id="pc1", name="Bard", zone_id="0,0", initiative=20,
            hp_max=30, hp_current=30,
        ),
        loader=BundledAssetLoader(),
    )
    return member.model_copy(
        update={'custom_counters': {KEY: {'spent': spent}}}
    )


def _fight(level: int, spent: int) -> object:
    handle = asyncio.run(
        O.start_combat(
            session_id="superior-inspiration",
            party=[_bard(level, spent)],
            encounter=[
                EncounterMemberSpec(
                    entity_id="m1", name="Foe", entity_type="Monster",
                    initiative=1, hp_current=9, hp_max=9, ac=10, zone_id='0,1',
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=2,
        )
    ).handle
    O.drain_pending_events(handle)
    return handle


def _spent(handle: object) -> int:
    live = O._get_live(handle)
    return live.custom_counters_by_entity.get('pc1', {}).get(KEY, {}).get('spent', 0)


def test_two_uses_remain_when_all_three_were_spent() -> None:
    """Бард 18 без применений приходит в бой с двумя."""
    handle = _fight(18, 3)
    assert _spent(handle) == 1, 'три применения из трёх → остаётся два, то есть spent == 1'


def test_a_bard_with_two_left_is_untouched() -> None:
    """Правило — пол, а не откат: с двумя оставшимися ничего не меняется."""
    handle = _fight(18, 1)
    assert _spent(handle) == 1


def test_the_feature_is_not_active_before_level_18() -> None:
    """До 18 уровня правила нет."""
    handle = _fight(17, 3)
    assert _spent(handle) == 3
