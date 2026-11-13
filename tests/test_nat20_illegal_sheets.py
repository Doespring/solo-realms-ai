"""Требование 5: законный лист не создаётся из несуществующих частей.

Пункт 5: «Нельзя создавать законный на вид лист с неработающими обязательными
способностями»; недопустимые сочетания выявляются «без зависаний и скрытого
исправления решения игрока».

Здесь проверяется отрицательная половина: каждый вымышленный компонент и
недопустимое сочетание ОТВЕРГАЮТСЯ, а не заменяются похожими.
"""
import pytest
from pydantic import ValidationError

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


BASE = dict(species_slug="human", class_slug="fighter", background_slug="soldier", level=1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("class_slug", "necromancer"),
        ("species_slug", "klingon"),
        ("background_slug", "astronaut"),
        ("subclass_slug", "not-a-subclass"),
    ],
)
def test_a_component_that_is_not_in_the_corpus_is_refused(loader, field, value):
    spec = CharacterBuildSpec(**{**BASE, field: value})
    with pytest.raises(ValueError):
        derive_sheet(spec, loader=loader)


def test_level_twentyone_is_refused():
    """20 — предел книги; уровень выше не «округляется» до 20."""
    with pytest.raises(ValidationError):
        CharacterBuildSpec(**{**BASE, "level": 21})


def test_level_zero_is_refused():
    with pytest.raises(ValidationError):
        CharacterBuildSpec(**{**BASE, "level": 0})


def test_a_subclass_of_another_class_is_refused(loader):
    """Чемпион — подкласс воина; у волшебника его быть не может."""
    spec = CharacterBuildSpec(**{**BASE, "class_slug": "wizard", "subclass_slug": "champion"})
    with pytest.raises(ValueError):
        derive_sheet(spec, loader=loader)


def test_the_refusal_names_the_problem(loader):
    """Отказ объясним: сообщение содержит имя отвергнутого слага."""
    spec = CharacterBuildSpec(**{**BASE, "class_slug": "necromancer"})
    with pytest.raises(ValueError, match="necromancer"):
        derive_sheet(spec, loader=loader)


def test_the_valid_baseline_really_builds(loader):
    """Положительный контроль: без него отказы ничего не доказывают."""
    sheet = derive_sheet(CharacterBuildSpec(**BASE), loader=loader)
    assert sheet.hp_max > 0
    assert sheet.features

