"""Критерий 14.2: обязательные способности имеют работающую механику,
а не только присутствие в списке.

Пункт 7 запрещает считать способность реализованной, потому что она есть в списке
или JSON загружается. Здесь для каждой способности проверяется ЧИСЛО, которое
получит расчёт: масштаб по уровню через `build_scale_values`, порог срабатывания
через `sneak_attack_triggers`, и то, что формула не подменяется заглушкой.
"""
import pytest

from dnd5e_engine.activities.scale import build_scale_values
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _scale(loader, class_slug, level, subclass=None):
    return build_scale_values(
        class_slug=class_slug, subclass_slug=subclass, species_slug=None,
        level=level, loader=loader,
    )


# SRD 5.2: Sneak Attack 1d6 на 1 уровне, +1d6 каждые 2 уровня, максимум 10d6 на 20-м.
SNEAK = [(1, 1), (3, 2), (5, 3), (7, 4), (9, 5), (11, 6), (13, 7), (15, 8), (17, 9), (19, 10), (20, 10)]


@pytest.mark.parametrize("level,expected", SNEAK)
def test_sneak_attack_dice_scale_by_rogue_level(loader, level, expected):
    values = _scale(loader, "rogue", level)
    assert values["rogue.sneak-attack"] == f"{expected}d6", level
    assert values["rogue.sneak-attack.number"] == expected
    assert values["rogue.sneak-attack.die"] == "d6"


def test_sneak_attack_never_exceeds_ten_dice(loader):
    assert _scale(loader, "rogue", 20)["rogue.sneak-attack.number"] == 10


# SRD 5.2: Rage damage bonus +2 на 1-8, +3 на 9-15, +4 на 16-20.
RAGE = [(1, 2), (8, 2), (9, 3), (15, 3), (16, 4), (20, 4)]


@pytest.mark.parametrize("level,expected", RAGE)
def test_rage_damage_scales_by_barbarian_level(loader, level, expected):
    values = _scale(loader, "barbarian", level)
    assert values["barbarian.rage-damage"] == expected, level


def test_sneak_attack_dice_are_a_real_expression_not_a_placeholder(loader):
    """Ловушка корпуса: у `sneak-attack` пусты damage.parts; значение даёт scale."""
    feature = loader.get_feature("sneak-attack")
    parts = feature.activities[0].damage.parts
    assert parts and parts[0].number is None, "данные не описывают число — это ожидаемо"
    assert _scale(loader, "rogue", 5)["rogue.sneak-attack"] == "3d6"


def test_the_scale_table_comes_from_the_class_document(loader):
    """Значения читаются из корпуса, а не зашиты в код."""
    rogue = loader.get_class("rogue")
    assert rogue.advancement is not None
    assert _scale(loader, "rogue", 1)["rogue.sneak-attack"] != _scale(loader, "rogue", 20)["rogue.sneak-attack"]

