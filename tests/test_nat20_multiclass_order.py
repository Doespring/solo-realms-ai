"""Порядок ключей `classes` не должен решать, законен ли мультикласс.

Найдено критиком: `_check_multiclass_abilities` брала `list(classes)[1:]`,
то есть «классом 1 уровня» считался ПЕРВЫЙ КЛЮЧ dict. Один и тот же персонаж
(Воин 1 / Волшебник 1 с СИЛ 8, ИНТ 15) принимался при `{"fighter":1,
"wizard":1}` и отвергался при `{"wizard":1,"fighter":1}`.

SRD 5.2 Multiclassing: порог 13 действует только для класса, В КОТОРЫЙ входят.
Класс, взятый на 1 уровне, порога не имеет. Стартовый класс задаётся полем
`class_slug` (`build_spec.py::_reconcile_classes`), а не порядком ключей.
"""
import pytest

from dnd5e_engine.build_spec import (
    CharacterBuildSpec,
    _check_multiclass_abilities,
    derive_sheet,
)
from dnd5e_srd_data.loader import BundledAssetLoader


#: Допустимый standard array (15, 14, 13, 12, 10, 8), но СИЛ 8 и ЛОВ 10:
#: воинский порог не пройден. ИНТ 15 пройден.
WEAK_FIGHTER = dict(strength=8, dexterity=10, constitution=13,
                    intelligence=15, wisdom=14, charisma=12)


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


@pytest.fixture(scope="module")
def common():
    return dict(species_slug="human", level=2, ability_scores=WEAK_FIGHTER,
                ability_score_method="standard_array", background_slug="soldier",
                equipment=())


@pytest.mark.parametrize("classes", [
    {"fighter": 1, "wizard": 1},
    {"wizard": 1, "fighter": 1},
])
def test_starting_as_fighter_is_legal_in_any_key_order(loader, classes):
    """Порядок ключей не меняет вердикт: воин стартовый, порога у него нет."""
    _check_multiclass_abilities(classes, WEAK_FIGHTER, loader, "fighter")


@pytest.mark.parametrize("classes", [
    {"fighter": 1, "wizard": 1},
    {"wizard": 1, "fighter": 1},
])
def test_starting_as_wizard_refuses_the_weak_fighter_in_any_key_order(loader, classes):
    """Входя в воина, нужно 13 в СИЛ или ЛОВ — здесь их нет."""
    with pytest.raises(ValueError, match="multiclass into 'fighter' needs 13 in one of"):
        _check_multiclass_abilities(classes, WEAK_FIGHTER, loader, "wizard")


def test_the_same_build_is_judged_by_class_slug_not_by_key_order(loader, common):
    """Через публичный путь `derive_sheet`: вердикт следует за class_slug."""
    for classes in ({"fighter": 1, "wizard": 1}, {"wizard": 1, "fighter": 1}):
        spec = CharacterBuildSpec(class_slug="fighter", classes=dict(classes), **common)
        derive_sheet(spec, loader=loader)  # воин стартовый — законно
    for classes in ({"wizard": 1, "fighter": 1}, {"fighter": 1, "wizard": 1}):
        spec = CharacterBuildSpec(class_slug="wizard", classes=dict(classes), **common)
        with pytest.raises(ValueError, match="multiclass into 'fighter'"):
            derive_sheet(spec, loader=loader)


def test_omitting_the_starting_class_falls_back_to_the_first_key(loader):
    """Вызовы без параметра сохраняют прежнее поведение (обратная совместимость)."""
    _check_multiclass_abilities({"fighter": 1, "wizard": 1}, WEAK_FIGHTER, loader)
    with pytest.raises(ValueError, match="multiclass into 'fighter'"):
        _check_multiclass_abilities({"wizard": 1, "fighter": 1}, WEAK_FIGHTER, loader)
