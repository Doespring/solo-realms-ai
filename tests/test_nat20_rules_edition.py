"""Требование 3: редакция правил сохраняется в самом персонаже.

§3: «Редакция и набор пакетов сохраняются в персонаже и кампании».

Идентичность релиза (`solo/nat20_content.py`) описывает, ЧТО загружено, но
живёт снаружи персонажа. Сохранённый лист сам должен сообщать, по какой
редакции он посчитан — иначе персонаж нельзя проверить после смены корпуса.

Поле `rules_edition` добавлено в `CharacterBuildSpec` и `DerivedSheet` со
значением по умолчанию `"5.2"`: это единственная редакция корпуса, поэтому
старые сохранения без поля читаются без миграции.
"""
import pytest
from pydantic import ValidationError

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _spec(**extra):
    base = dict(
        species_slug="human",
        class_slug="fighter",
        level=1,
        ability_scores=dict(
            strength=15, dexterity=14, constitution=13,
            intelligence=12, wisdom=10, charisma=8,
        ),
        ability_score_method="standard_array",
        background_slug="soldier",
        equipment=(),
    )
    base.update(extra)
    return CharacterBuildSpec(**base)


def test_the_build_spec_carries_the_edition():
    assert _spec().rules_edition == "5.2"


def test_the_derived_sheet_carries_the_edition(loader):
    sheet = derive_sheet(_spec(), loader=loader)
    assert sheet.rules_edition == "5.2"


def test_the_edition_survives_a_save_and_reload(loader):
    spec = _spec()
    payload = spec.model_dump(mode="json")
    assert payload["rules_edition"] == "5.2"
    again = CharacterBuildSpec(**payload)
    assert again.rules_edition == "5.2"
    assert derive_sheet(again, loader=loader).rules_edition == "5.2"


def test_an_old_save_without_the_field_still_loads():
    """Совместимость: сохранение, записанное до появления поля."""
    payload = _spec().model_dump(mode="json")
    payload.pop("rules_edition")
    restored = CharacterBuildSpec(**payload)
    assert restored.rules_edition == "5.2"


def test_the_edition_travels_into_a_multiclass_sheet(loader):
    spec = _spec(
        classes={"fighter": 1, "wizard": 1},
        level=2,
        class_slug="fighter",
        ability_scores=dict(
            strength=13, dexterity=14, constitution=12,
            intelligence=15, wisdom=10, charisma=8,
        ),
    )
    assert derive_sheet(spec, loader=loader).rules_edition == "5.2"


def test_an_edition_the_corpus_does_not_publish_is_refused():
    """Требование 2: редакция не берётся по имени и не подменяется похожей.

    Первая версия этого теста утверждала, что `rules_edition="2014"` проходит.
    Это закрепляло дефект: лист объявлял редакцию, которой в корпусе нет, и
    выглядел законным. Материал 2014 в поставку не входит, поэтому чужая
    редакция отвергается, а не пересчитывается по 5.2 молча.
    """
    with pytest.raises(ValidationError, match="не опубликована"):
        _spec(rules_edition="2014")


def test_the_supported_edition_round_trips():
    spec = _spec(rules_edition="5.2")
    assert spec.model_dump(mode="json")["rules_edition"] == "5.2"

