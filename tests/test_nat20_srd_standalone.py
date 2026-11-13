"""Требование 3, критерий 14.6: SRD работает самостоятельно.

Пункт 3: «PHB не требуется для обычной SRD-кампании»; «PHB-пакет не включается в
распространяемую сборку автоматически».

Проверяется фактом: полный цикл создания персонажа проходит на одном SRD-корпусе,
и никакого PHB-пакета в репозитории нет. Второе — не пробел, а состояние: явное
подключение PHB описано в разделе ограничений отчёта.
"""
import pytest

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


BASE = dict(class_slug="fighter")


STANDARD = dict(
    strength=15, dexterity=14, constitution=13,
    intelligence=12, wisdom=10, charisma=8,
)


def _build(loader, **overrides):
    spec = CharacterBuildSpec(
        species_slug="human",
        level=1,
        ability_scores=STANDARD,
        ability_score_method="standard_array",
        background_slug="soldier",
        equipment=(),
        **{**BASE, **overrides},
    )
    return derive_sheet(spec, loader=loader)


def test_an_srd_character_is_created_without_any_extension(loader):
    sheet = _build(loader)
    assert sheet.hp_max == 11
    assert sheet.ac == 12
    assert sheet.features
    assert sheet.proficiency_bonus == 2


def test_the_srd_corpus_declares_only_the_2024_edition(loader):
    """Ни одной записи 2014 — смешения нет по построению корпуса."""
    for slug in loader.list_slugs("classes"):
        assert "5.2" in loader.get_class(slug).provenance.srd_version
    for slug in loader.list_slugs("spells"):
        assert "5.2" in loader.get_spell(slug).provenance.srd_version


def test_srd_coverage_is_complete_for_every_class(loader):
    """Каждый из 12 классов SRD проходим без расширений."""
    for slug in loader.list_slugs("classes"):
        sheet = _build(loader, class_slug=slug)
        assert sheet.features, slug
        assert sheet.hp_max > 0, slug


def test_the_highest_level_is_reachable_on_srd_alone(loader):
    spec = CharacterBuildSpec(
        species_slug="human", class_slug="fighter", level=20,
        ability_scores=STANDARD, ability_score_method="standard_array",
        background_slug="soldier", equipment=(),
    )
    sheet = derive_sheet(spec, loader=loader)
    assert sheet.proficiency_bonus == 6
    assert sheet.extra_attack_count == 3


def test_the_repository_ships_no_phb_package():
    """Пакет не включён в сборку — требование 3 выполнено буквально."""
    from pathlib import Path
    import dnd5e_srd_data

    root = Path(__file__).resolve().parents[1]
    packages = list((root / "vendor" / "nat20" / "packages").glob("*"))
    names = [p.name for p in packages if p.is_dir()]
    assert names == ["dnd5e-engine", "dnd5e-srd-data"], names

