"""r162: маркер источника `srd`/`phb` у каждой записи, включая существующие id.

Решение владельца 01.11: маркер стоит и в id старых правил, а не только у
новых. Схема — `phb2024.<глава>.<тема>` для купленной книги и
`srd2024.<категория>.<slug>` для SRD 5.2.1, плюс `source.kind` рядом с id.
Одна выборка по маркеру делит корпус на SRD-часть (CC-BY-4.0) и PHB-часть
(книга владельца).

Здесь два разных стража:

* данные корпуса (`rules_data/v2/chapters/**`) — маркер обязателен у всех 332
  записей, id и `source.kind` обязаны совпадать;
* проекция движка (каталог Nat20) — файлы пакета не патчатся, маркер
  приклеивает хост-слой, и он обязан быть у каждой записи каталога.
"""
import json
import re
from pathlib import Path

import pytest

from solo import nat20_content as content
from solo.nat20_canonical import PHB_ROOT

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "rules_data/v2"
CHAPTERS = sorted((V2 / "chapters").glob("*/*.json"))
#: Старый префикс собирается из частей: этот файл сам попадает под приёмочный
#: grep, и литерал в нём дал бы ненулевой счётчик у исправного дерева.
LEGACY_PREFIX = "dnd2024" + ".phb"

needs_phb = pytest.mark.skipif(
    not PHB_ROOT.is_dir(),
    reason="нет integration/nat20/phb-2024/subclasses (приватные данные книги): "
           "собрать scripts/build_phb_subclasses.py",
)


def _corpus():
    rows = []
    for path in CHAPTERS:
        rows.extend(json.loads(path.read_text(encoding="utf-8"))["rules"])
    return rows


CORPUS = _corpus()


def test_every_corpus_record_carries_its_source_marker():
    """332 записи, и у каждой marker(id) == marker(source.kind)."""
    assert len(CORPUS) == 332, len(CORPUS)
    for record in CORPUS:
        marker = content.corpus_marker(record)
        assert marker["kind"] == "phb", marker
        assert marker["id"].startswith("phb2024.phb"), marker
        assert marker["provenance"] == "phb-2024-ru" and marker["license"] == "user_private", marker


def test_corpus_counts_are_stated_not_implied():
    """Счёт по маркерам: пока в корпусе только PHB, и это видно числом."""
    kinds = [content.corpus_marker(record)["kind"] for record in CORPUS]
    assert (kinds.count("phb"), kinds.count("srd")) == (332, 0)
    # SRD-записей нет ни одной: если первая появится, счёт изменится здесь,
    # а не «незаметно» в каталоге.
    assert not [record for record in CORPUS if record["id"].startswith("srd2024.")]


def test_coverage_index_uses_the_same_marked_ids():
    """Индекс покрытия ссылается на размеченные id и ни на что старое."""
    coverage = json.loads((V2 / "coverage/phb-01.json").read_text(encoding="utf-8"))
    corpus_ids = {record["id"] for record in CORPUS}
    indexed = [row["id"] for row in coverage["rules"]]
    assert indexed and set(indexed) <= corpus_ids, set(indexed) - corpus_ids
    assert all(rule_id.startswith("phb2024.phb01.") for rule_id in coverage["adapter_rule_ids"])
    for row in coverage["rules"]:
        # pytest_node параметризован id правила: узел обязан звать новый id.
        assert row["id"] in row["pytest_node"], row


def test_guard_rejects_a_record_without_source_kind():
    broken = dict(CORPUS[0], source={key: value for key, value in CORPUS[0]["source"].items()
                                     if key != "kind"})
    with pytest.raises(ValueError, match="source.kind"):
        content.corpus_marker(broken)


def test_guard_rejects_a_legacy_unmarked_id():
    record = next(row for row in CORPUS if row["id"].startswith("phb2024.phb01."))
    broken = dict(record, id=LEGACY_PREFIX + record["id"][len("phb2024.phb"):])
    with pytest.raises(ValueError, match="marker"):
        content.corpus_marker(broken)


def test_guard_rejects_a_kind_that_contradicts_the_id():
    record = next(row for row in CORPUS if row["id"].startswith("phb2024.phb"))
    lying = dict(record, source=dict(record["source"], kind="srd"))
    with pytest.raises(ValueError, match="srd"):
        content.corpus_marker(lying)


def test_no_record_in_the_tree_still_carries_the_legacy_prefix():
    """Приёмка карточки: ноль вхождений старого префикса во всём дереве."""
    pattern = re.compile(re.escape(LEGACY_PREFIX))
    skip = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", "runtime"}
    hits = []
    for path in ROOT.rglob("*"):
        if any(part in skip for part in path.parts) or not path.is_file():
            continue
        if path.suffix not in (".json", ".py", ".js") or path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            hits.append(path.relative_to(ROOT).as_posix())
    assert hits == [], hits


def test_catalog_projection_marks_every_engine_record_as_srd():
    """Записи пакета — SRD по определению: лицензия задана манифестом релиза.

    Вендор не патчится: метку приклеивает хост-слой
    (`nat20_content.engine_marker`) в проекции каталога.
    """
    from solo.nat20_prototype import CATEGORIES, catalog_batch

    packet = catalog_batch(CATEGORIES)
    entries = packet["entries"]
    assert entries, "каталог пуст"
    for row in entries:
        marker = row["source_marker"]
        assert marker["kind"] == "srd", row["id"]
        assert marker["provenance"] == "srd-5.2.1" and marker["license"] == "CC-BY-4.0", row["id"]
        # Запись не из книги владельца: маркер PHB здесь невозможен.
        assert not marker.get("printed_pages"), row["id"]


def test_engine_marker_reads_the_record_not_the_caller():
    """Признак PHB берётся из самой записи, иначе каталог соврёт о книге."""
    srd = content.engine_marker("lore", {"source": "foundry", "license_tag": "CC-BY-4.0"})
    assert (srd["kind"], srd["provenance"]) == ("srd", "srd-5.2.1")
    phb = content.engine_marker("phb2024.bard.valor",
                                {"source": "phb-2024-ru", "license_tag": "user_private",
                                 "printed_pages": [57, 58]})
    assert (phb["kind"], phb["provenance"], phb["license"]) == ("phb", "phb-2024-ru", "user_private")
    assert phb["printed_pages"] == [57, 58]
    # Slug с маркером книги не становится SRD даже без provenance.
    assert content.engine_marker("phb2024.bard.dance")["kind"] == "phb"


@needs_phb
def test_phb_subclass_records_report_the_same_kind():
    """Наши файлы подклассов из r147 несут тот же маркер `phb`, а не только строку."""
    from solo.nat20_canonical import records

    found = records()
    assert found
    for record in found:
        assert record.marker()["kind"] == "phb", record.slug
