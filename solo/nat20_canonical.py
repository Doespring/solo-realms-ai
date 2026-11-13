"""r147: подклассы, которых нет в SRD-наборе (PHB 2024), без правки пина.

SRD 5.2 несёт ровно один подкласс на класс (`canonical/subclasses/` — 12
файлов), поэтому «Коллегия доблести», «очарования» и «танца» движку неизвестны
и `derive_sheet` честно отвечает `unknown subclass`. Здесь эти записи живут
рядом с релизом, а не в пине:

* данные — `integration/nat20/phb-2024/subclasses/<slug>.json`, в git НЕ идут
  (текст купленной книги), собираются `scripts/build_phb_subclasses.py`;
* маркер источника — `provenance.source='phb-2024-ru'`,
  `license_tag='user_private'` + напечатанные страницы. SRD-записи пакета
  остаются `source='foundry'`, `license_tag='CC-BY-4.0'`; разделить два набора
  можно одной выборкой по `provenance.source`;
* модель `Provenance` пакета — `Literal['foundry']` / `Literal['CC-BY-4.0']`,
  поэтому врать в неё нельзя: `PhbSubclass` наследует `Subclass`, но объявляет
  собственный `PhbProvenance`. Правка пакета/вендора не нужна.

Шов — загрузчик: `PhbSubclassLoader` сначала отдаёт запись пакета, и только если
её нет — нашу. Пакет остаётся авторитетом для SRD-слагов (`lore` и остальные
11), поэтому подмена одного набора другим невозможна.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from dnd5e_srd_data.loader import BundledAssetLoader, Category
from dnd5e_srd_data.schema.class_ import Subclass
from dnd5e_srd_data.schema.common import ReviewState
from dnd5e_srd_data.schema.feature import Feature

ROOT = Path(__file__).resolve().parents[1]
PHB_ROOT = ROOT / "integration/nat20/phb-2024/subclasses"
MARKER = "phb-2024-ru"
LICENSE = "user_private"
KIND = "phb"

#: Умения книги, которые движок уже исполняет под каноническим слагом пина.
#:
#: `extra_attack_count` и боевой гейт считают ровно слаги `extra-attack`,
#: `two-extra-attacks`, `three-extra-attacks` (`rules/character.py:73`), а
#: запись книги называет то же умение `phb2024.bard.valor.extra-attack`. Без
#: этой таблицы Коллегия доблести на 6 уровне получала одну атаку вместо двух:
#: текст в листе был, механики нет. Здесь только те умения, чей напечатанный
#: абзац совпадает с пин-записью (сверено по книге), — иначе игрок получил бы
#: механику чужого умения.
CANONICAL_EQUIVALENTS = {
    "phb2024.bard.valor.extra-attack": "extra-attack",
}


class PhbProvenance(BaseModel, frozen=True):
    """Источник записи PHB 2024. Отдельный тип: `Provenance` пакета допускает
    только Foundry/CC-BY, и подставлять туда книгу значило бы соврать в пине."""

    #: r162: тот же маркер источника, что в корпусе (`source.kind`). У PHB он
    #: один по определению, но поле нужно: лист/каталог читают `kind`, а не
    #: разбирают строку источника.
    kind: Literal["phb"] = KIND
    source: Literal["phb-2024-ru"] = MARKER
    license_tag: Literal["user_private"] = LICENSE
    document_id: str
    document_sha256: str
    printed_pages: tuple[int, ...]
    pdf_pages: tuple[int, ...]
    section: str

    def marker(self) -> dict[str, object]:
        """Проекция маркера для листа/каталога: одна выборка отделяет PHB от SRD."""
        return dict(kind=self.kind, source=self.source, license=self.license_tag,
                    document_id=self.document_id, printed_pages=list(self.printed_pages),
                    pdf_pages=list(self.pdf_pages), section=self.section)


class PhbFeatureText(BaseModel, frozen=True):
    """Умение подкласса как напечатано: имя, уровень, страницы, дословный текст.

    Исполняемых полей (`activities`, `passive_effects`) здесь нет намеренно:
    типизированной механики в книге нет, а выдумывать её нельзя. Пока умение
    остаётся текстом — это `adjudication_required`, и это видно в `text`.
    """

    slug: str
    name: str
    level: int
    printed_pages: tuple[int, ...]
    text: str
    #: Что движок делает с умением. Сейчас единственное честное значение —
    #: текст правила: типизированной активности у этих умений нет.
    execution_status: Literal["adjudication_required"] = "adjudication_required"
    blocker: str


def with_canonical_equivalents(record: PhbSubclass) -> PhbSubclass:
    """Дополнить запись подкласса каноническими слагами, которые движок исполняет.

    Движок узнаёт механику не по тексту, а по слагу пина (`extra-attack` →
    вторая атака). Если книга называет то же умение под своим слагом, копия
    записи получает ещё и канонический грант того же уровня — тогда лист и
    бой считают эффект, а исходный слаг остаётся ради маркера и страницы.
    """
    grants = list(record.granted_features)
    known = {str(grant.slug) for grant in grants}
    added = []
    for grant in grants:
        alias = CANONICAL_EQUIVALENTS.get(str(grant.slug))
        if alias and alias not in known:
            added.append(grant.model_copy(update={"slug": alias}))
            known.add(alias)
    if not added:
        return record
    return record.model_copy(update={"granted_features": [*grants, *added]})


class PhbFeature(Feature):
    """r196: умение подкласса PHB 2024 как запись движку, а не пропуск в stderr.

    До r196 у этих 135 умений не было `Feature` в пине, и `derive_sheet`
    писал в stderr «granted feature slug did not resolve to a canonical
    feature; skipping» — игрок не знал, что умение потеряно. Запись есть в
    книге (текст, страницы), поэтому загрузчик отдаёт её как `Feature` с
    маркером `phb-2024-ru` и ПУСТЫМИ `activities`/`passive_effects`: механики
    в книге нет, выдумывать её нельзя. `review.known_divergence` несёт
    напечатанную причину, `source_slug` — подкласс-владелец.
    """

    provenance: PhbProvenance
    feature_type: Literal["subclass_feature"] = "subclass_feature"
    source_slug: str = ""

    def marker(self) -> dict[str, object]:
        return self.provenance.marker()


class PhbSubclass(Subclass):
    """`Subclass` пакета + маркер PHB и дословные тексты умений."""

    provenance: PhbProvenance
    features: list[PhbFeatureText] = Field(default_factory=list)

    def marker(self) -> dict[str, object]:
        return self.provenance.marker()


@lru_cache(maxsize=1)
def _records() -> dict[str, PhbSubclass]:
    """Все PHB-подклассы из каталога данных; нет каталога — пусто.

    Каталог приватный (в git не едет), поэтому отсутствие данных — штатное
    состояние чистого клона, а не ошибка: SRD-набор продолжает работать.
    """
    if not PHB_ROOT.is_dir():
        return {}
    out: dict[str, PhbSubclass] = {}
    for path in sorted(PHB_ROOT.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        record = PhbSubclass.model_validate(body)
        if record.slug != path.stem:
            raise ValueError(f"PHB subclass file/name mismatch: {path.name} names {record.slug!r}")
        if record.provenance.source != MARKER or record.provenance.license_tag != LICENSE:
            raise ValueError(f"PHB subclass without source marker: {record.slug}")
        out[record.slug] = record
    return out


def records() -> list[PhbSubclass]:
    return sorted(_records().values(), key=lambda row: row.slug)


def get(slug: str) -> PhbSubclass | None:
    """Запись по id. Идентификатор несёт маркер источника (`phb2024.bard.valor`).

    Короткое имя (`valor`, `champion`) принимается как псевдоним: так подклассы
    назывались в постановках до маркировки id. Псевдоним только читает
    существующую запись — новых id не создаёт; неоднозначное имя (`dance` есть
    и у Барда) не угадывается.
    """
    key = str(slug)
    found = _records().get(key)
    if found is None and "." not in key:
        found = _records().get("phb2024.bard." + key)
    if found is None and "." not in key:
        matches = [row for name, row in _records().items() if name.endswith("." + key)]
        found = matches[0] if len(matches) == 1 else None
    return found


def marker(slug: str) -> dict[str, object] | None:
    """Маркер источника для проекции; `None` — запись не наша (SRD или нет её).

    Понимает и подкласс, и его умение: лист перечисляет именно умения, и
    страница книги нужна там же, где стоит имя умения.
    """
    record = get(slug)
    if record is not None:
        return record.marker()
    for record in _records().values():
        for feature in record.features:
            if feature.slug == slug:
                found = record.marker()
                found["printed_pages"] = list(feature.printed_pages)
                found["feature_level"] = feature.level
                return found
    return None


def feature_name(slug: str) -> str | None:
    """Имя PHB-умения для листа: у этих слагов нет своих `Feature` в пакете."""
    found = feature_text(slug)
    return None if found is None else found[0].name


@lru_cache(maxsize=1)
def _feature_texts() -> dict[str, tuple[PhbFeatureText, PhbSubclass]]:
    """Индекс «слаг умения → (текст книги, подкласс-владелец)».

    Считается один раз на каталог: `get_feature` зовут в циклах (пассивные
    изменения, шкалы, репертуар), и обход 36 записей на каждый слаг был бы
    лишней работой на каждом листе.
    """
    out: dict[str, tuple[PhbFeatureText, PhbSubclass]] = {}
    for record in _records().values():
        for feature in record.features:
            out[feature.slug] = (feature, record)
    return out


def feature_text(slug: str) -> tuple[PhbFeatureText, PhbSubclass] | None:
    """Текст книги для слага умения; `None` — такого умения в книге нет."""
    return _feature_texts().get(str(slug))


def book_feature(slug: str) -> PhbFeature | None:
    """Умение книги как `Feature` движка: запись есть, типизированной механики нет.

    Маркер источника и напечатанные страницы берутся у самого умения
    (`printed_pages` записи подкласса — это диапазон подкласса, а не абзаца),
    причина «не автоматизировано» — в `review.known_divergence`. Модель
    `Feature` морозить нельзя (в пакете она изменяемая), поэтому копия
    отдаётся наружу каждый раз.
    """
    found = feature_text(slug)
    if found is None:
        return None
    text, record = found
    source = record.provenance
    return PhbFeature(
        slug=text.slug,
        name=text.name,
        description=text.text,
        feature_type="subclass_feature",
        source_slug=record.slug,
        provenance=PhbProvenance(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            printed_pages=tuple(text.printed_pages),
            pdf_pages=tuple(source.pdf_pages),
            section=f"{source.section} · {text.name}",
        ),
        review=ReviewState(known_divergence=text.blocker),
    )


class PhbSubclassLoader(BundledAssetLoader):
    """Пакетный загрузчик + PHB-подклассы; остальные категории не тронуты.

    `slugs` хранится экземпляром, а не читается из `__init__` пакета: подпись
    `BundledAssetLoader(*, root=None)` та же, так что подмена прозрачна.
    """

    def get_subclass(self, slug: str) -> Subclass | None:
        found = super().get_subclass(slug)
        if found is not None:
            return found
        record = get(slug)
        return None if record is None else with_canonical_equivalents(record)

    def get_feature(self, slug: str) -> Feature | None:
        """r196: пакет — авторитет, затем умение книги как `Feature`.

        Раньше здесь был `None`, и `derive_sheet` молча выбрасывал 135
        умений PHB-подклассов из расчёта пассивных изменений. Теперь запись
        находится (с маркером источника и страницей), а то, что она не даёт
        механики, видно в `activities`/`passive_effects` и в
        `review.known_divergence`, а не только в stderr.
        """
        found = super().get_feature(slug)
        if found is not None:
            return found
        return book_feature(slug)

    def list_slugs(self, category: Category) -> list[str]:
        slugs = super().list_slugs(category)
        if category == "subclasses":
            slugs = sorted(set(slugs) | set(_records()))
        return slugs
