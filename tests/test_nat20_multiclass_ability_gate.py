"""Требование 5: вердикт мультикласса не зависит от порядка ключей словаря.

БАГ, найденный раундом 35: `derive_sheet` не передавал `starting_class` в
`_check_multiclass_abilities`, и проверка брала `next(iter(classes))`.
Одни и те же характеристики (STR=8, INT=13) давали:

    {"barbarian": 2, "wizard": 2}  -> ПРИНЯТ   (варвар стартовый, не проверяется)
    {"wizard": 2, "barbarian": 2}  -> отказ    (варвар мультикласс, STR>=13)

То есть решение игрока подменялось порядком ключей — ровно то, что
запрещает п.5: «Недопустимые сочетания выявляются без скрытого исправления
решения игрока».
"""
import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


# STR=8 < 13 — варвар как МУЛЬТИКЛАСС обязан быть отвергнут.
# INT=13 хватает волшебнику, поэтому стартовый волшебник законен.
WEAK = AbilityScores(
    strength=8, dexterity=10, constitution=14,
    intelligence=13, wisdom=12, charisma=14,
)


def _build(classes, loader, **kw):
    spec = CharacterBuildSpec(
        species_slug="human", classes=classes, background_slug="soldier",
        level=4, ability_scores=WEAK, **kw,
    )
    return derive_sheet(spec, loader=loader)


def test_an_explicit_starting_class_fixes_the_verdict_regardless_of_key_order(loader):
    """С `starting_class="wizard"` оба порядка обязаны отвергнуть варвара."""
    for order in (["barbarian", "wizard"], ["wizard", "barbarian"]):
        with pytest.raises(ValueError, match="multiclass into"):
            _build({slug: 2 for slug in order}, loader, starting_class="wizard")


def test_a_legal_multiclass_is_still_accepted(loader):
    """Отрицательные случаи не должны сломать законный лист."""
    strong = AbilityScores(
        strength=14, dexterity=10, constitution=14,
        intelligence=13, wisdom=12, charisma=14,
    )
    spec = CharacterBuildSpec(
        species_slug="human", classes={"barbarian": 2, "wizard": 2},
        starting_class="wizard", background_slug="soldier",
        level=4, ability_scores=strong,
    )
    assert derive_sheet(spec, loader=loader).spell_slots


def test_the_starting_class_is_recorded_on_the_spec(loader):
    """Поле должно читаться обратно: иначе сохранение теряет решение."""
    spec = CharacterBuildSpec(
        species_slug="human", classes={"wizard": 2, "barbarian": 2},
        starting_class="wizard", background_slug="soldier", level=4,
        ability_scores=WEAK,
    )
    assert spec.starting_class == "wizard"
