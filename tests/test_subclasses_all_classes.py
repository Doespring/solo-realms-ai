"""r169: у каждого класса подкласс книги даёт умения, а не `unknown subclass`.

SRD 5.2 несёт один подкласс на класс, а книга — четыре. После r147 это было
закрыто только для Барда: выбор «Школа магии», «Домен», «Круг друида» упирался
в `unknown subclass`, потому что записей 36 остальных подклассов не было.
r169 собирает их генератором (`scripts/build_phb_subclasses.py`) из того же
извлечённого текста книги и с тем же маркером источника.

Что проверяется:

* состав: на каждый из 12 классов корпус называет четыре подкласса, и каждый
  из них либо приходит из пакета SRD, либо есть в каталоге книги — ни одного
  безымянного;
* тот самый дефект: на уровне получения подкласса `derive_sheet` выдаёт его
  умения для КАЖДОГО класса, без `unknown subclass`;
* форма записи: умения — дословный текст со страницами и честным статусом
  `adjudication_required`, id несут маркер книги, уровни не ниже 3.

Данные приватные: без каталога `integration/nat20/phb-2024/subclasses`
скипаются только тесты книги (маркер `needs_phb`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts"))

from nat20_builds import legal  # noqa: E402

import build_phb_subclasses as builder  # noqa: E402  — тот же список классов, что у генератора
from solo.nat20_canonical import (CANONICAL_EQUIVALENTS, PHB_ROOT, PhbSubclass,  # noqa: E402
                                  PhbSubclassLoader, records)
from solo.nat20_prototype import DATA  # noqa: E402

CHAPTERS = ROOT / "rules_data/v2/chapters/phb-03-classes"
LOADER = PhbSubclassLoader(root=DATA)
SRD_TWINS = 12

needs_phb = pytest.mark.skipif(
    not PHB_ROOT.is_dir(),
    reason="нет integration/nat20/phb-2024/subclasses (приватные данные книги): "
           "собрать scripts/build_phb_subclasses.py",
)


def corpus() -> dict[str, list[str]]:
    """{class_slug: [подклассы книги]} из корпуса глав «Классы персонажей»."""
    out = {}
    for path in sorted(CHAPTERS.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        rule = next(row for row in body["rules"]
                    if ".podklass_" in row["id"] and row.get("facts", {}).get("subclasses"))
        class_ru = rule["name"].split(":", 1)[0].strip()
        out[builder.CLASS_SLUG[class_ru]] = list(rule["facts"]["subclasses"])
    return out


BOOK = corpus()
CLASSES = tuple(sorted(BOOK))


def phb_subclasses(class_slug: str) -> list[str]:
    return [row.slug for row in records() if row.class_identifier == class_slug]


#: Порядок, в котором раскладываются повышения характеристик в высокоуровневой
#: сборке: `legal()` кладёт все +2 в первую характеристику пула и упирается
#: в предел 20 (ValueError на 12 уровне) — на 3 уровне этого не видно.
ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")


def build(class_slug: str, subclass: str | None = None, level: int = 3) -> CharacterBuildSpec:
    """Легальная сборка: подкласс — поле, а не выбор."""
    payload = dict(class_slug=class_slug, species_slug="human", level=level,
                   equipment=("dagger", "leather-armor"))
    if subclass is not None:
        payload["subclass_slug"] = subclass
    spec = legal(**payload)
    if level > 3:
        picks, turn = [], 0
        for pick in spec["selected_choices"]:
            if pick.startswith("asi:"):
                _, owner, at, rest = pick.split(":")
                pick = f"asi:{owner}:{at}:{ABILITIES[turn % len(ABILITIES)]}+{rest.split('+')[1]}"
                turn += 1
            picks.append(pick)
        spec["selected_choices"] = picks
    return CharacterBuildSpec.model_validate(spec)


def test_the_corpus_names_four_subclasses_for_every_class():
    """Корпус — источник списка: по четыре подкласса на каждый из 12 классов."""
    assert len(BOOK) == 12, sorted(BOOK)
    for class_slug, names in BOOK.items():
        assert len(names) == 4, (class_slug, names)


@needs_phb
def test_every_subclass_the_corpus_names_exists_in_the_engine():
    """Ни одного безымянного: имя корпуса либо SRD-пара, либо запись книги."""
    missing = []
    for class_slug, names in BOOK.items():
        srd_names = {name for (slug, name) in builder.SRD if slug == class_slug}
        ours = {row.name for row in records() if row.class_identifier == class_slug}
        missing += [(class_slug, name) for name in names
                    if name not in srd_names and name not in ours]
        assert len(srd_names) == 1, (class_slug, srd_names)
    assert not missing, missing


@needs_phb
def test_the_package_keeps_its_twelve_srd_subclasses():
    """Наш каталог не подменяет пакет: 12 SRD-слагов на месте и без маркера книги."""
    package = [row for row in LOADER.list_slugs("subclasses") if not row.startswith("phb2024.")]
    assert len(package) == SRD_TWINS, package
    for slug in package:
        assert not isinstance(LOADER.get_subclass(slug), PhbSubclass), slug


@needs_phb
def test_every_class_has_at_least_one_subclass_of_its_own_outside_srd():
    """Смысл карточки: у каждого класса есть подкласс вне SRD-набора."""
    for class_slug in CLASSES:
        ours = phb_subclasses(class_slug)
        assert ours, class_slug
        assert all(LOADER.get_subclass(row).class_identifier == class_slug for row in ours)


@needs_phb
@pytest.mark.parametrize("class_slug", CLASSES)
def test_a_subclass_of_every_class_grants_features_at_its_gate(class_slug):
    """Тот самый дефект: на уровне получения подкласс обязан дать умения."""
    subclass = phb_subclasses(class_slug)[0]
    record = LOADER.get_subclass(subclass)
    plain = set(derive_sheet(build(class_slug), loader=LOADER).features)
    gained = set(derive_sheet(build(class_slug, subclass), loader=LOADER).features) - plain
    # На 3 уровне выдаются только умения этого уровня: старшие придут на своих.
    opening = {ref.slug for ref in record.granted_features if ref.level <= 3}
    assert opening, subclass
    assert opening <= gained, (subclass, sorted(gained))


@needs_phb
def test_every_book_subclass_gives_a_sheet_without_unknown_subclass():
    """Полный прогон: все подклассы книги строятся, ни одного `unknown subclass`."""
    broken, total = [], 0
    for record in records():
        total += 1
        top = max(ref.level for ref in record.granted_features)
        try:
            sheet = derive_sheet(build(record.class_identifier, record.slug, level=top),
                                 loader=LOADER)
        except Exception as error:  # noqa: BLE001 — нужен весь список, а не первая ошибка
            broken.append((record.slug, f"{type(error).__name__}: {error}"))
            continue
        wanted = {ref.slug for ref in record.granted_features}
        if not wanted <= set(sheet.features):
            broken.append((record.slug, sorted(wanted - set(sheet.features))))
    assert total >= 36, total
    assert not broken, broken


@needs_phb
@pytest.mark.parametrize("class_slug", CLASSES)
def test_each_class_record_is_a_verbatim_text_with_its_own_source_marker(class_slug):
    """Запись книги: свой класс, маркер источника, дословный текст, Мастер-статус."""
    for slug in phb_subclasses(class_slug):
        record = LOADER.get_subclass(slug)
        assert isinstance(record, PhbSubclass), slug
        assert record.marker()["source"] == "phb-2024-ru", slug
        assert record.marker()["printed_pages"], slug
        assert record.description.strip(), slug
        assert record.granted_features, slug
        # r196: объявленный канонический алиас (Extra Attack) добавляет грант
        # без нового абзаца книги; всё остальное обязано совпадать один-к-одному.
        printed = {feature.slug for feature in record.features}
        granted = {str(ref.slug) for ref in record.granted_features}
        aliases = {CANONICAL_EQUIVALENTS[name] for name in printed
                   if name in CANONICAL_EQUIVALENTS}
        assert granted == printed | aliases, slug
        for feature in record.features:
            assert feature.slug.startswith(slug + "."), (slug, feature.slug)
            assert feature.level >= 3, (slug, feature.slug, feature.level)
            assert feature.text.strip(), feature.slug
            assert feature.printed_pages, feature.slug
            assert feature.execution_status == "adjudication_required", feature.slug
            assert feature.name in feature.blocker, (feature.slug, feature.blocker)


@needs_phb
def test_every_class_subclass_is_offered_by_the_choice_step():
    """Экран выбора подкласса предлагает подклассы книги вместе с пакетными."""
    from solo.nat20_wizard import WizardDraft, _subclass_step

    for class_slug in CLASSES:
        step = _subclass_step(WizardDraft(class_slug=class_slug, level=3), LOADER)
        assert step is not None, class_slug
        options = {row["id"]: row for row in step["options"]}
        for slug in phb_subclasses(class_slug):
            assert slug in options, (class_slug, slug)
            assert options[slug]["detail"].startswith("PHB 2024, стр. "), options[slug]


@needs_phb
def test_no_rule_line_was_dropped_from_the_record():
    """Ни одна строка правил не потерялась при чистке полосы.

    Чистка снимает колонтитулы, номера страниц и подписи иллюстраций; ошибка в
    её фильтре молча съедает текст правила (так уже было: правило «строка в
    кавычках» выбросило ««Варианты приёмов» (далее в описании этого…»). Здесь
    каждая строка раздела, которую разбор не счёл подписью, обязана найтись в
    тексте записи — с точностью до переносов, которые чистка склеивает.
    """
    import re

    def skeleton(text: str) -> str:
        return re.sub(r"[^0-9a-zA-Zа-яё]+", "", text.lower())

    # Сверяем со всеми записями класса: умение, напечатанное под чужим
    # заголовком (Следопыт, FEATURE_OWNER), живёт в соседней записи — терять
    # его всё равно нельзя.
    by_class: dict[str, list] = {}
    for record in records():
        by_class.setdefault(record.class_identifier, []).append(record)
    missing = []
    for section in builder.sections():
        own = by_class.get(section["class_slug"], [])
        if not any(row.name == section["name"] for row in own):
            continue  # SRD-пара: её текст живёт в пакете, а не у нас
        haystack = skeleton(" ".join(
            row.description + " " + " ".join(feature.text for feature in row.features)
            for row in own))
        for page, line in section["rows"]:
            text = line.strip()
            if not text or builder.COLOPHON.match(text) or builder.furniture(text):
                continue
            if text == section["name"] or builder.HEADING.match(text):
                continue
            if len(skeleton(text)) >= 4 and skeleton(text) not in haystack:
                missing.append((section["name"], page, text))
    assert not missing, missing[:10]
