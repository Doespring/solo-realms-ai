"""Требование 5: языки и инструменты предыстории доходят до ЛИСТА.

Найдено вручную: `derive_sheet` возвращал `languages == ()` для ЛЮБОГО персонажа,
хотя `canonical/backgrounds/*.json` содержит `languages: ["common"]`. Движок читал
языки только из эффектов предметов (`activities/passive_stats.py:79`, ключ
`system.traits.languages.value`), а грант предыстории не доходил никуда.
Поля `tool_proficiencies` у листа не было вовсе.

Нарушение п.5: «Нельзя создавать законный на вид лист с неработающими
обязательными способностями» — лист выглядел полным, но владения не было.
"""
import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader


SCORES = AbilityScores(
    strength=10, dexterity=14, constitution=14,
    intelligence=16, wisdom=12, charisma=8,
)


def _sheet(background, loader):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="wizard", background_slug=background,
        level=1, ability_scores=SCORES,
    )
    return derive_sheet(spec, loader=loader)


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def test_a_background_language_reaches_the_sheet(loader):
    """Каждая предыстория SRD 5.2 даёт Common; лист обязан это показать."""
    for background in ("sage", "soldier", "criminal", "acolyte"):
        sheet = _sheet(background, loader)
        assert "common" in sheet.languages, f"{background}: язык Common не дошёл до листа"


def test_the_language_list_matches_the_corpus(loader):
    """Языки берутся из корпуса, а не из встроенного перечня."""
    corpus = loader.get_background("sage").languages
    sheet = _sheet("sage", loader)
    assert tuple(corpus) == tuple(sheet.languages)


def test_a_background_tool_proficiency_reaches_the_sheet(loader):
    """Инструменты в кодах корпуса (`art:calligrapher`, `thief`, `game:*`)."""
    assert _sheet("sage", loader).tool_proficiencies == ("art:calligrapher",)
    assert _sheet("criminal", loader).tool_proficiencies == ("thief",)
    assert _sheet("soldier", loader).tool_proficiencies == ("game:*",)


def test_an_open_tool_choice_is_not_decided_for_the_player(loader):
    """`game:*` — «любой игровой набор». Движок обязан сохранить звёздочку,
    а не выбрать конкретный набор за игрока (п.5: не скрыто исправлять выбор)."""
    tools = _sheet("soldier", loader).tool_proficiencies
    assert any("*" in t for t in tools), "открытый выбор подменён конкретным инструментом"


def test_without_a_background_nothing_is_invented(loader):
    """Нет предыстории — нет языков и инструментов. Подстановки быть не должно."""
    sheet = _sheet(None, loader)
    assert sheet.languages == ()
    assert sheet.tool_proficiencies == ()

