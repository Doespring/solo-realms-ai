"""Требование 5: развитие через ASI и черты, положительные и отрицательные случаи.

Форма выбора здесь НЕ «просто слага»: ASI и черты задаются токенами
`asi:<class>:<level>:<ability>+<n>` и `feat:<class>:<level>:<slug>`
(см. `build_spec.py:830-835`). При первой пробе я передал `grappler` слагом,
получил отказ «is not an option of any feature choice» и поспешно решил, что
выбора черт нет вовсе. Выбор есть; ошибочен был вызов.

Отрицательные случаи важны не меньше: без них «принято» не доказывает
ни проверки уровня ASI, ни проверки категории черты.
"""
import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader


BASE = dict(strength=16, dexterity=14, constitution=14,
            intelligence=10, wisdom=12, charisma=8)


def _fighter(level, picks):
    spec = CharacterBuildSpec(
        species_slug="human", classes={"fighter": level},
        background_slug="soldier", level=level,
        ability_scores=AbilityScores(**BASE), selected_choices=picks,
    )
    return derive_sheet(spec, loader=BundledAssetLoader())


def test_an_asi_token_raises_the_chosen_ability():
    """PHB 2024: Ability Score Improvement даёт +2 или +1/+1."""
    sheet = _fighter(4, ("asi:fighter:4:strength+2",))
    assert sheet.ability_scores.strength == 18, "16 + 2"


def test_a_feat_token_grants_the_feat():
    """Черта 4 уровня приходит слотом ASI того же уровня."""
    sheet = _fighter(4, ("feat:fighter:4:grappler",))
    assert "grappler" in sheet.feats


def test_an_asi_slot_requires_the_level_that_grants_it():
    """У бойца нет ASI на 1 уровне: брать черту не на чем (п.5, п.8)."""
    with pytest.raises(ValueError, match="no Ability Score Improvement"):
        _fighter(1, ("feat:fighter:1:grappler",))


def test_an_epic_boon_names_its_character_level_requirement():
    """Boon of Truesight — 19 уровень; отказ обязан назвать требование."""
    with pytest.raises(ValueError, match="19"):
        _fighter(4, ("feat:fighter:4:boon-of-truesight",))


def test_the_asi_token_is_required_with_a_reason():
    """Попытка взять ASI через feat: отвергается с подсказкой о форме токена."""
    with pytest.raises(ValueError, match="asi:"):
        _fighter(4, ("feat:fighter:4:ability-score-improvement",))


def test_a_prose_ability_requirement_is_enforced():
    """SRD 5.2 Grappler: «Strength or Dexterity 13+».

    Требование записано В ТЕКСТЕ (`FeatPrerequisite.requirement`), а не полем.
    До этого исправления движок текст не читал вовсе: лист с STR 10 и DEX 10
    получал Grappler и выглядел законным (п.5 — «законный на вид лист с
    неработающими обязательными способностями»).
    """
    weak = dict(strength=10, dexterity=10, constitution=14,
                intelligence=12, wisdom=12, charisma=10)
    spec = CharacterBuildSpec(
        species_slug="human", classes={"fighter": 4},
        background_slug="soldier", level=4,
        ability_scores=AbilityScores(**weak),
        selected_choices=("feat:fighter:4:grappler",),
    )
    with pytest.raises(ValueError, match="needs 13 in one of"):
        derive_sheet(spec, loader=BundledAssetLoader())


def test_a_prose_requirement_accepts_either_named_ability():
    """«Strength OR Dexterity» — достаточно одной; DEX 14 при STR 10 хватает."""
    dex_only = dict(strength=10, dexterity=14, constitution=14,
                    intelligence=12, wisdom=12, charisma=10)
    spec = CharacterBuildSpec(
        species_slug="human", classes={"fighter": 4},
        background_slug="soldier", level=4,
        ability_scores=AbilityScores(**dex_only),
        selected_choices=("feat:fighter:4:grappler",),
    )
    sheet = derive_sheet(spec, loader=BundledAssetLoader())
    assert "grappler" in sheet.feats
