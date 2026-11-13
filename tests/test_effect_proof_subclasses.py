"""r147: коллегии Барда, которых нет в SRD-наборе, дают умения, а не `unknown subclass`.

SRD 5.2 несёт один подкласс на класс (`canonical/subclasses/` — 12 файлов), и
`derive_sheet` на `subclass_slug='glamour'` честно падал `unknown subclass`.
Записи «Коллегии доблести», «очарования» и «танца» живут вне пина
(`integration/nat20/phb-2024/subclasses/`, в git не идут), загрузчик
`PhbSubclassLoader` отдаёт сначала пакет, потом их.

Что этот файл доказывает:

* каждый из четырёх подклассов Барда собирается на 3 уровне и приносит свои
  умения, а ниже 3 уровня отвергается движком;
* SRD-слаг (`lore`) по-прежнему обслуживается пакетом с provenance Foundry;
  записи PHB несут маркер `phb-2024-ru` / `user_private` и печатные страницы;
* маркер доезжает до проекций: каталог Nat20 и лист персонажа называют
  источник умения, а не только файл данных;
* у каждого PHB-умения есть дословный текст, и его статус — решение Мастера,
  потому что типизированной активности в книге нет.

Данные приватные: без каталога `integration/nat20/phb-2024/subclasses` в skip
уходят только PHB-тесты (маркер `needs_phb`), а проверка пакетного SRD-слага
выполняется всегда — чистый клон обязан оставаться зелёным и не терять
проверку того, что пакет не подменён.
"""
from __future__ import annotations

import pytest

from dnd5e_engine.build_spec import AbilityScores, CharacterBuildSpec, derive_sheet
from dnd5e_srd_data.schema.class_ import Subclass

from solo.nat20_canonical import (CANONICAL_EQUIVALENTS, PHB_ROOT, PhbSubclass,
                                  PhbSubclassLoader, records)
from solo.nat20_prototype import DATA

SCORES = AbilityScores(strength=13, dexterity=13, constitution=13,
                       intelligence=13, wisdom=13, charisma=13)
LOADER = PhbSubclassLoader(root=DATA)

#: Четыре коллегии книги; `lore` — SRD-овская, она уже в пакете.
SRD_COLLEGE = "lore"
PHB_COLLEGES = ("phb2024.bard.valor", "phb2024.bard.glamour", "phb2024.bard.dance")

#: PHB-данные приватные (в git не идут): без них скипаются только PHB-тесты.
needs_phb = pytest.mark.skipif(
    not PHB_ROOT.is_dir(),
    reason="нет integration/nat20/phb-2024/subclasses (приватные данные книги): "
           "собрать scripts/build_phb_subclasses.py",
)


def spec(subclass, level=3):
    return CharacterBuildSpec(species_slug="human", background_slug="soldier",
                              class_slug="bard", classes={"bard": level}, level=level,
                              ability_scores=SCORES, subclass_slug=subclass)


def test_the_package_still_serves_its_own_subclass():
    """Пакет — авторитет для SRD-слагов: `lore` не подменяется нашим набором."""
    lore = LOADER.get_subclass(SRD_COLLEGE)
    assert isinstance(lore, Subclass) and not isinstance(lore, PhbSubclass)
    assert lore.provenance.source == "foundry"
    assert lore.provenance.license_tag == "CC-BY-4.0"
    assert SRD_COLLEGE in LOADER.list_slugs("subclasses")


@needs_phb
def test_phb_subclasses_are_found_and_carry_their_source_marker():
    slugs = LOADER.list_slugs("subclasses")
    for slug in PHB_COLLEGES:
        assert slug in slugs, (slug, slugs)
        record = LOADER.get_subclass(slug)
        assert isinstance(record, PhbSubclass), slug
        assert record.slug == slug
        assert record.class_identifier == "bard"
        assert record.provenance.source == "phb-2024-ru"
        assert record.provenance.license_tag == "user_private"
        assert record.provenance.printed_pages, slug
        assert record.provenance.document_sha256, slug
        # Маркер есть и в проекции записи, а не только в файле.
        assert record.marker()["source"] == "phb-2024-ru"


@pytest.mark.parametrize("subclass", [
    pytest.param(SRD_COLLEGE, id="srd-" + SRD_COLLEGE),
    *(pytest.param(slug, id=slug, marks=needs_phb) for slug in PHB_COLLEGES),
])
def test_every_college_grants_features_at_third_level(subclass):
    """Тот самый дефект: на 3 уровне подкласс обязан дать умения, не ValueError."""
    without = set(derive_sheet(spec(None), loader=LOADER).features)
    with_sub = set(derive_sheet(spec(subclass), loader=LOADER).features)
    gained = with_sub - without
    assert gained, (subclass, sorted(with_sub))


@needs_phb
@pytest.mark.parametrize("subclass", PHB_COLLEGES)
def test_a_college_is_refused_below_its_gate(subclass):
    """SRD 5.2: подкласс берётся на 3 уровне класса, раньше — отказ движка."""
    with pytest.raises(ValueError, match="needs .* level 3"):
        derive_sheet(spec(subclass, level=1), loader=LOADER)


@needs_phb
@pytest.mark.parametrize("subclass", PHB_COLLEGES)
def test_every_phb_feature_is_verbatim_text_marked_for_the_master(subclass):
    record = LOADER.get_subclass(subclass)
    levels = {ref.level for ref in record.granted_features}
    assert levels <= {3, 6, 14}, (subclass, levels)
    # r196: у умения книги может быть канонический аналог в пине (Extra
    # Attack): тогда грантов на один больше, чем напечатанных абзацев — это
    # объявленный алиас, а не пропавший текст.
    printed = {feature.slug for feature in record.features}
    granted = {str(ref.slug) for ref in record.granted_features}
    aliases = {CANONICAL_EQUIVALENTS[slug] for slug in printed if slug in CANONICAL_EQUIVALENTS}
    assert granted == printed | aliases, subclass
    for feature in record.features:
        assert feature.slug.startswith("phb2024."), feature.slug
        assert feature.text.strip(), feature.slug
        assert feature.printed_pages, feature.slug
        # Типизированной активности у этих умений нет: честный статус — Мастер.
        assert feature.execution_status == "adjudication_required", feature.slug
        assert feature.name in feature.blocker, (feature.slug, feature.blocker)


@needs_phb
def test_the_features_are_the_printed_ones():
    """Имена умений — как в книге (PDF 57/58-60), не выдуманные слаги."""
    expected = {
        "phb2024.bard.valor": ["Боевое вдохновение", "Боевая подготовка",
                               "Дополнительная атака", "Боевая магия"],
        "phb2024.bard.glamour": ["Чарующая магия", "Мантия вдохновения",
                                 "Мантия величия", "Несокрушимое величие"],
        "phb2024.bard.dance": ["Ослепительные фуэте", "Вдохновляющее перемещение",
                               "Совместный танец", "Ведущее уклонение"],
    }
    for slug, names in expected.items():
        record = LOADER.get_subclass(slug)
        assert [feature.name for feature in record.features] == names, slug
        assert record.name == {"phb2024.bard.valor": "Коллегия доблести",
                               "phb2024.bard.glamour": "Коллегия очарования",
                               "phb2024.bard.dance": "Коллегия танца"}[slug]


@needs_phb
def test_the_subclass_step_offers_the_colleges_with_their_source():
    """Экран выбора подкласса: коллегии книги в списке и с маркером источника."""
    from solo.nat20_wizard import WizardDraft, _subclass_step

    step = _subclass_step(WizardDraft(class_slug="bard", level=3), LOADER)
    options = {row["id"]: row for row in step["options"]}
    assert SRD_COLLEGE in options
    assert options[SRD_COLLEGE]["detail"] is None  # пакетный SRD — без маркера книги
    for slug in PHB_COLLEGES:
        assert slug in options, (slug, sorted(options))
        assert options[slug]["detail"].startswith("PHB 2024, стр. "), options[slug]


@needs_phb
def test_the_sheet_projection_names_the_source_of_a_phb_feature():
    """Игрок видит не слаг, а имя умения и страницу книги."""
    from solo.nat20_wizard import _sheet_projection

    sheet = derive_sheet(spec("phb2024.bard.valor"), loader=LOADER)
    rows = {row["id"]: row for row in _sheet_projection(sheet, LOADER)["features"]}
    feature = rows["phb2024.bard.valor.combat-inspiration"]
    assert feature["name"] == "Боевое вдохновение"
    assert feature["source"] == "PHB 2024, стр. 56"
    # Пакетное умение маркера не получает.
    rows = {row["id"]: row for row in _sheet_projection(derive_sheet(spec(SRD_COLLEGE), loader=LOADER), LOADER)["features"]}
    assert rows["cutting-words"]["source"] is None


@needs_phb
def test_the_two_sets_split_by_provenance_in_one_query():
    """Команда разделения SRD/PHB: выборка по маркеру источника."""
    package = [row for row in LOADER.list_slugs("subclasses") if not row.startswith("phb2024.")]
    ours = [row for row in LOADER.list_slugs("subclasses") if row.startswith("phb2024.")]
    assert len(package) == 12, package
    # r147 закрыл три коллегии, r169 — остальные 33 подкласса книги: набор книги
    # должен совпадать с каталогом целиком и не пересекаться с пакетным SRD.
    assert set(ours) == {row.slug for row in records()}
    assert set(PHB_COLLEGES) <= set(ours)
    assert not set(ours) & set(package)
