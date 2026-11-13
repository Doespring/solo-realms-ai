"""r196: умение подкласса либо доходит до листа, либо объясняется причиной.

До r196 `derive_sheet` на каждое выданное умение, которого нет в пине, писал в
stderr «granted feature slug did not resolve to a canonical feature; skipping»
и шёл дальше. Прогон по 48 подклассам каталога давал 135 таких строк — все в
PHB-подклассах (`phb2024.*`), ноль в SRD. Игрок не знал, что потерял, а движок
не считал: ни числа пропусков, ни имени умения в листе.

Теперь у каждого выданного умения есть разбор (`solo.nat20_content.
granted_feature_journal`): `pin` — типизированная запись пина, `book` — умение
книги (текст и напечатанная страница есть, эффект не автоматизирован,
`adjudication_required`), `missing` — нет ни записи пина, ни текста книги.

Файл держит четыре обещания:

* ни одно выданное умение не пропадает без записи (`missing == 0`) и в журнал
  сборки не уходит ни одной строки «did not resolve»;
* умение книги объясняется строкой «эффект не автоматизирован» с напечатанной
  страницей, а не тишиной;
* запись книги не выдумывает механику: `activities`/`passive_effects` пусты,
  поэтому лист считает ровно то же, что и до r196 (сверка двух загрузчиков);
* экран сборки получает эту строку в `warnings`, а не только лог.

Данные PHB 2024 приватные (в git не идут). Без каталога
`integration/nat20/phb-2024/subclasses` проверки про книгу честно пропускаются,
а SRD-набор остаётся зелёным.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.loader import BundledAssetLoader

from solo import nat20_content
from solo.nat20_canonical import (PHB_ROOT, PhbFeature, PhbSubclassLoader, feature_text,
                                  get as phb_record)
from solo.nat20_content import (derive_sheet_with_journal, feature_plan,
                                granted_feature_journal, granted_feature_warnings)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402

#: Число умений, которые до r196 пропадали из расчёта при обходе 48 подклассов
#: на 12 уровне (мой прогон: 135 строк «did not resolve»).
BOOK_GRANTED_AT_12 = 135

#: Те же числа по классам, что дал прогон 03.11 (до правки — 135).
BOOK_BY_CLASS = {"barbarian": 11, "bard": 9, "cleric": 12, "druid": 12, "fighter": 11,
                 "monk": 12, "paladin": 10, "ranger": 13, "rogue": 9, "sorcerer": 10,
                 "warlock": 14, "wizard": 12}

LEVEL = 12


def _loader():
    from solo.nat20_prototype import RELEASE
    return nat20_content.release(RELEASE)[0]


def _spec(subclass):
    loader = _loader()
    record = loader.get_subclass(subclass)
    return CharacterBuildSpec(species_slug="dwarf",
                              classes={record.class_identifier: LEVEL},
                              subclass_slug=subclass)


def _subclasses(loader):
    return sorted(loader.list_slugs("subclasses"))


def _needs_book():
    if not PHB_ROOT.is_dir():
        pytest.skip("нет каталога integration/nat20/phb-2024/subclasses (приватные данные книги)")


def _canonical(value):
    """Сравнимый вид листа: порядок `frozenset` в JSON зависит от хеш-сида."""
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


class _PinOnlyLoader(PhbSubclassLoader):
    """Загрузчик до r196: пина запись есть, книги и алиасов нет (для сверки)."""

    def get_subclass(self, slug):
        found = BundledAssetLoader.get_subclass(self, slug)
        return found if found is not None else phb_record(slug)

    def get_feature(self, slug):
        return BundledAssetLoader.get_feature(self, slug)


# ---------------------------------------------------------------------------
# 1. Ни одного молчаливого пропуска: 48 подклассов, 0 «did not resolve»
# ---------------------------------------------------------------------------
def test_every_granted_feature_resolves_across_all_subclasses(caplog):
    caplog.set_level(logging.WARNING, logger="dnd5e_engine.build_spec")
    loader = _loader()
    totals = {"scanned": 0, "pin": 0, "book": 0, "missing": 0}
    by_class = {}
    for subclass in _subclasses(loader):
        spec = _spec(subclass)
        sheet, journal = derive_sheet_with_journal(spec, loader=loader)
        assert journal["scanned"] == len(set(sheet.features)), subclass
        assert journal["missing"] == 0, (subclass, journal["rows"]["missing"])
        for key in totals:
            totals[key] += journal[key]
        by_class[loader.get_subclass(subclass).class_identifier] = by_class.get(
            loader.get_subclass(subclass).class_identifier, 0) + journal["book"]
    skips = [row for row in caplog.records if "did not resolve" in row.getMessage()]
    assert skips == [], [row.getMessage() for row in skips[:5]]
    assert totals["missing"] == 0
    assert totals["scanned"] == totals["pin"] + totals["book"]
    if PHB_ROOT.is_dir():
        assert totals["book"] == BOOK_GRANTED_AT_12, totals
        assert {k: v for k, v in sorted(by_class.items())} == BOOK_BY_CLASS
    else:
        assert totals["book"] == 0


# ---------------------------------------------------------------------------
# 2. Умение книги объясняется, а не молчит: статус, страница, маркер
# ---------------------------------------------------------------------------
def test_book_features_are_explained_with_printed_pages():
    _needs_book()
    loader = _loader()
    explained = 0
    for subclass in _subclasses(loader):
        if not subclass.startswith("phb2024."):
            continue
        _, journal = derive_sheet_with_journal(_spec(subclass), loader=loader)
        assert journal["rows"]["missing"] == []
        for row in journal["rows"]["book"]:
            explained += 1
            assert row["status"] == "adjudication_required", row
            assert row["printed_pages"], row
            assert all(isinstance(page, int) and page > 0 for page in row["printed_pages"])
            assert row["source"]["kind"] == "phb" and row["source"]["source"] == "phb-2024-ru"
            assert row["subclass"] == subclass
            assert "не автоматизирован" in row["reason"]
            assert str(row["printed_pages"][0]) in row["reason"]
    assert explained == BOOK_GRANTED_AT_12


def test_srd_subclasses_have_no_book_rows():
    loader = _loader()
    for subclass in _subclasses(loader):
        if subclass.startswith("phb2024."):
            continue
        _, journal = derive_sheet_with_journal(_spec(subclass), loader=loader)
        assert journal["book"] == 0, (subclass, journal["book"])
        assert journal["scanned"] == journal["pin"]


# ---------------------------------------------------------------------------
# 3. Запись книги не выдумывает механику: лист считает то же, что до r196
# ---------------------------------------------------------------------------
def test_book_features_add_no_invented_mechanics():
    _needs_book()
    loader = _loader()
    checked = 0
    for subclass in _subclasses(loader):
        _, journal = derive_sheet_with_journal(_spec(subclass), loader=loader)
        for row in journal["rows"]["book"]:
            feature = loader.get_feature(row["id"])
            assert isinstance(feature, PhbFeature), row["id"]
            assert feature.activities == [] and feature.passive_effects == [], row["id"]
            assert feature.feature_type == "subclass_feature"
            assert feature.source_slug == subclass
            assert feature.review.known_divergence, row["id"]
            assert feature.provenance.printed_pages == tuple(row["printed_pages"])
            checked += 1
    assert checked == BOOK_GRANTED_AT_12


def test_sheet_numbers_are_unchanged_by_book_records():
    """Старый загрузчик (без записей книги) даёт тот же лист, кроме одного алиаса."""
    loader = _loader()
    old = _PinOnlyLoader()
    allowed = {"phb2024.bard.valor": {"features", "extra_attack_count"}}
    differing = {}
    for subclass in _subclasses(loader):
        spec = _spec(subclass)
        now = _canonical(derive_sheet(spec, loader=loader).model_dump(mode="json"))
        was = _canonical(derive_sheet(spec, loader=old).model_dump(mode="json"))
        if now != was:
            differing[subclass] = {key for key in now if now[key] != was.get(key)}
    assert differing == allowed


def test_canonical_alias_delivers_the_implemented_mechanic():
    """Коллегия доблести получает вторую атаку: умение книги совпало с пином."""
    _needs_book()
    loader = _loader()
    spec = _spec("phb2024.bard.valor")
    sheet = derive_sheet(spec, loader=loader)
    assert "phb2024.bard.valor.extra-attack" in sheet.features
    assert "extra-attack" in sheet.features
    assert sheet.extra_attack_count == 1
    for subclass in ("phb2024.bard.dance", "lore"):
        other = derive_sheet(_spec(subclass), loader=loader)
        assert other.extra_attack_count == 0, subclass
        assert "extra-attack" not in other.features, subclass


# ---------------------------------------------------------------------------
# 4. План умения книги и экран сборки говорят то же, что журнал
# ---------------------------------------------------------------------------
def test_feature_plan_names_the_book_page_and_the_master():
    _needs_book()
    loader = _loader()
    sample = []
    for subclass in _subclasses(loader):
        _, journal = derive_sheet_with_journal(_spec(subclass), loader=loader)
        sample.extend(row["id"] for row in journal["rows"]["book"])
    assert len(sample) == BOOK_GRANTED_AT_12
    for slug in sample:
        plan = feature_plan(slug)
        assert plan is not None, slug
        assert plan["status"] == "master", (slug, plan["status"])
        assert plan["source"] == "phb-2024-ru", slug
        assert "не автоматизирован" in plan["reason"], slug
        text, _ = feature_text(slug)
        assert str(text.printed_pages[0]) in plan["reason"], slug
        assert plan["activities"] == []


def test_builder_preview_shows_the_honest_line():
    _needs_book()
    from solo.nat20_api import Nat20GameEngine
    build = legal(class_slug="bard", species_slug="human", level=3,
                  subclass_slug="phb2024.bard.valor")
    preview = Nat20GameEngine.preview_build(build)
    journal = preview["granted_features"]
    assert journal["book"] > 0 and journal["missing"] == 0
    lines = granted_feature_warnings(journal)
    assert lines and "эффект не автоматизирован" in lines[0]
    assert any("эффект не автоматизирован" in str(row) for row in preview["warnings"])
    assert set(journal["rows"]) == {"pin", "book", "missing"}


def test_journal_counts_distinct_slugs_once():
    loader = _loader()
    sheet, _ = derive_sheet_with_journal(_spec("lore"), loader=loader)
    doubled = granted_feature_journal([*sheet.features, *sheet.features], loader=loader)
    assert doubled["scanned"] == len(set(sheet.features))
    assert doubled["pin"] + doubled["book"] + doubled["missing"] == doubled["scanned"]
