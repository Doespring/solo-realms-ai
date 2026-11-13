"""Требование 5: языки приходят из ВСЕХ источников корпуса, а не из одного.

Найдено отрицательным примером: друид 1 уровня с предысторией sage получал
`languages == ["common"]`, хотя SRD 5.2 Druid 1 — «You know Druidic, the secret
language of Druids». Движок читал языки только из эффектов предметов
(`passive.languages`); `advancement[type=Trait].configuration.grants` не читался.

Источники языков в корпусе (проверено поиском по всем 1663 файлам):
- предыстории: `languages` — у всех 4;
- классы: `Trait`-гранты `languages:*` — только druid, ranger, rogue;
- виды: НЕТ ни одной записи (0 из 14) — поэтому вид языков не даёт.
"""
import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader


SCORES = AbilityScores(
    strength=10, dexterity=14, constitution=14,
    intelligence=16, wisdom=12, charisma=8,
)


def _languages(cls, level=1, background="sage", species="elf-high"):
    spec = CharacterBuildSpec(
        species_slug=species, class_slug=cls, background_slug=background,
        level=level, ability_scores=SCORES,
    )
    return list(derive_sheet(spec, loader=BundledAssetLoader()).languages)


def test_a_druid_knows_druidic():
    """SRD 5.2 Druidic: «You know Druidic». Trait-грант languages:exotic:druidic."""
    assert "druidic" in _languages("druid")


def test_a_rogue_knows_thieves_cant():
    """SRD 5.2 Rogue 1: «You know Thieves' Cant». Грант languages:exotic:cant."""
    assert "cant" in _languages("rogue")


def test_a_ranger_language_stays_an_open_choice():
    """Следопыт: languages:standard:* и languages:exotic:* — ВЫБОР игрока.

    Движок обязан сохранить открытость: подстановка конкретного языка была бы
    скрытым исправлением выбора игрока (п.5).
    """
    langs = _languages("ranger")
    assert "*" not in langs
    assert not any(x in langs for x in ("elvish", "dwarvish", "orc")), langs


def test_a_two_part_open_choice_adds_no_language():
    """Плут: grants = ["languages:*", "languages:exotic:cant"].

    Двухчастная форма `languages:*` — открытый выбор, а не язык с именем ""
    и не пустая строка в списке. Берётся только `cant`.
    """
    langs = _languages("rogue")
    assert langs == ["common", "cant"], langs


def test_a_class_without_language_grants_adds_nothing():
    """Волшебник Trait-языков не имеет — лишнего не появляется."""
    assert _languages("wizard") == ["common"]


def test_the_background_language_is_merged_not_replaced():
    """Язык предыстории сохраняется рядом с классовым, а не затирается."""
    assert _languages("druid") == ["common", "druidic"]


def test_druidic_arrives_at_the_level_that_grants_it():
    """Грант стоит на 1 уровне — раньше его быть не может, позже он есть."""
    assert "druidic" in _languages("druid", level=1)
    assert "druidic" in _languages("druid", level=5)

