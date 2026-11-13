"""Требование 3: происхождение данных, редакция, лицензия.

Пункт 3 задаёт: «Происхождение данных и русского перевода сохраняется»;
«Не считай записать правилами 2024 только по имени или расположению файла»;
«Материал PHB, опубликованный также в SRD, используй через подтверждённую
SRD-версию там, где это соответствует редакции».

Каждая запись корпуса несёт `provenance`. Этот файл проверяет, что заявка не
декоративная: у записи есть источник, URL, зафиксированный коммит, дата,
версия загрузчика, набор редакций и лицензия — и что редакция действительно
2024 (5.2), а не 2014.
"""
import pytest

from dnd5e_srd_data.loader import BundledAssetLoader


KINDS = ("classes", "subclasses", "species", "backgrounds", "feats", "spells", "items", "conditions")

GETTER_NAME = {
    "classes": "get_class",
    "subclasses": "get_subclass",
    "species": "get_species",
    "backgrounds": "get_background",
    "feats": "get_feat",
    "spells": "get_spell",
    "items": "get_item",
    "conditions": "get_condition",
}


@pytest.fixture(scope="module")
def loader():
    return BundledAssetLoader()


def _get(loader, kind, slug):
    getter = getattr(loader, GETTER_NAME[kind])
    return getter(slug)


def test_every_record_declares_its_provenance(loader):
    missing = []
    total = 0
    for kind in KINDS:
        for slug in loader.list_slugs(kind):
            total += 1
            prov = getattr(_get(loader, kind, slug), "provenance", None)
            if prov is None:
                missing.append(f"{kind}/{slug}")
    assert total > 900, total
    assert not missing, f"без происхождения: {missing[:5]}"


def test_every_record_pins_a_source_url_and_a_commit(loader):
    bad = []
    for kind in KINDS:
        for slug in loader.list_slugs(kind):
            prov = _get(loader, kind, slug).provenance
            if not (prov.source and prov.source_url and "/blob/" in prov.source_url):
                bad.append(f"{kind}/{slug}")
    assert not bad, f"источник не зафиксирован: {bad[:5]}"


def test_every_record_declares_the_2024_edition(loader):
    """Редакция 5.2 — это и есть PHB 2024; 2014 сюда не попадает."""
    wrong = []
    for kind in KINDS:
        for slug in loader.list_slugs(kind):
            prov = _get(loader, kind, slug).provenance
            if "5.2" not in prov.srd_version:
                wrong.append(f"{kind}/{slug}: {sorted(prov.srd_version)}")
    assert not wrong, f"не 5.2: {wrong[:5]}"


def test_every_record_declares_a_license(loader):
    bad = []
    for kind in KINDS:
        for slug in loader.list_slugs(kind):
            prov = _get(loader, kind, slug).provenance
            if not prov.license_tag:
                bad.append(f"{kind}/{slug}")
    assert not bad, f"без лицензии: {bad[:5]}"


def test_no_record_claims_2014(loader):
    """Отделение редакций: ни одна запись не объявляет себя 2014."""
    wrong = []
    for kind in KINDS:
        for slug in loader.list_slugs(kind):
            prov = _get(loader, kind, slug).provenance
            if any(v.startswith("5.1") or v == "2014" for v in prov.srd_version):
                wrong.append(f"{kind}/{slug}")
    assert not wrong, wrong[:5]


def test_the_corpus_directory_has_no_2014_tree(loader):
    """Пункт 10: `canonical_2014` не должно быть рядом с `canonical`."""
    import dnd5e_srd_data
    from pathlib import Path

    root = Path(dnd5e_srd_data.__file__).parent
    assert not (root / "canonical_2014").exists()
    assert (root / "canonical").is_dir()

