"""Требование 5: экспертиза доходит до листа и ограничена квотой ПРАВИЛ.

Найдено отрицательным примером: плут 1 с двумя токенами `expertise:` давал
`skill_expertise == frozenset()` — верный отказ «есть ли владение» проходил,
но сам выбор в лист не попадал. С четырьмя токенами лист тоже принимался:
квота из корпуса (`Trait` с `"mode": "expertise"`, `choices[count=2]`)
не читалась вовсе.
"""
import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader


SCORES = AbilityScores(
    strength=10, dexterity=16, constitution=14,
    intelligence=12, wisdom=10, charisma=8,
)
FOUR_SKILLS = (
    "skill:stealth", "skill:sleight-of-hand", "skill:acrobatics", "skill:perception",
)


def _rogue(level, picks):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="rogue", background_slug="criminal",
        level=level, ability_scores=SCORES, selected_choices=picks,
    )
    return derive_sheet(spec, loader=BundledAssetLoader())


def test_chosen_expertise_reaches_the_sheet():
    """SRD 5.2 §Expertise: «You gain Expertise in two skills of your choice».

    Без этого поля лист выглядел законным, но терял выбранные игроком
    способности (требование 5: неработающие обязательные способности).
    """
    sheet = _rogue(1, FOUR_SKILLS + ("expertise:stealth", "expertise:acrobatics"))
    assert sheet.skill_expertise == frozenset({"stealth", "acrobatics"})


def test_a_single_expertise_is_also_recorded():
    """Квота — верхняя граница, а не обязанность исчерпать её."""
    sheet = _rogue(1, FOUR_SKILLS + ("expertise:stealth",))
    assert sheet.skill_expertise == frozenset({"stealth"})


def test_expertise_beyond_the_level_quota_is_refused():
    """Плут 1 по книге выбирает ДВЕ экспертизы; четыре — отказ с числом."""
    with pytest.raises(ValueError, match="exceeds"):
        _rogue(1, FOUR_SKILLS + (
            "expertise:stealth", "expertise:acrobatics",
            "expertise:perception", "expertise:sleight-of-hand",
        ))


def test_the_quota_grows_at_level_six():
    """Плут 6 добавляет ещё две: четыре экспертизы на шестом уровне законны."""
    sheet = _rogue(6, FOUR_SKILLS + (
        "expertise:stealth", "expertise:acrobatics",
        "expertise:perception", "expertise:sleight-of-hand",
    ))
    assert len(sheet.skill_expertise) == 4


def test_expertise_without_proficiency_is_refused():
    """Нельзя получить экспертизу в навыке, которым не владеешь."""
    with pytest.raises(ValueError, match="needs proficiency"):
        _rogue(1, FOUR_SKILLS + ("expertise:history",))


def test_a_class_without_expertise_refuses_any_pick():
    """У волшебника экспертизы нет: квота 0, любой выбор — отказ."""
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug="sage",
        level=1, ability_scores=SCORES,
        selected_choices=("expertise:arcana",),
    )
    with pytest.raises(ValueError):
        derive_sheet(spec, loader=BundledAssetLoader())

