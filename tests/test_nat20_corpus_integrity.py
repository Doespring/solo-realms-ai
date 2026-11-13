"""Целостность канонического корпуса SRD 5.2.

`vendor/nat20/PATCHES.json` закрепляет только 19 изменённых нами файлов кода:
все 1663 записи корпуса оставались вне контроля целостности. Требование 10
называет это прямо — «дополнительные данные вне контроля целостности».
"
`vendor/nat20/CORPUS.json` закрепляет каждый файл и сводный отпечаток. Этот файл
проверяет, что:

1. ни один файл корпуса не изменён и не удалён;
2. не появилось незаявленных файлов;
3. сводный отпечаток сходится с построчным;
4. механические грузы (`activities`) не потерялись при регенерации.
"
Пункт 4 — прямой ответ на «риск потери механических модификаторов при
регенерации»: отпечаток поймает изменение ЛЮБОГО байта, а счётчик `activities`
поймает регрессию генератора, которая сохранила файлы, но выпотрошила механику.
"""
import hashlib
import json
from pathlib import Path

import pytest


VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "nat20"
CORPUS = VENDOR / "CORPUS.json"


@pytest.fixture(scope="module")
def manifest():
    return json.loads(CORPUS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def root(manifest):
    return VENDOR / manifest["root"]


def _digest(files):
    hasher = hashlib.sha256()
    for rel in sorted(files):
        hasher.update(rel.encode())
        hasher.update(b"\0")
        hasher.update(bytes.fromhex(files[rel]))
    return hasher.hexdigest()


def test_the_manifest_covers_the_whole_corpus(manifest, root):
    assert manifest["file_count"] == 1663
    assert len(manifest["files"]) == manifest["file_count"]
    on_disk = {
        str(p.relative_to(root)).replace("\\", "/"): p for p in root.rglob("*.json")
    }
    missing = sorted(set(manifest["files"]) - set(on_disk))
    extra = sorted(set(on_disk) - set(manifest["files"]))
    assert not missing, f"файлы заявлены, но отсутствуют: {missing[:5]}"
    assert not extra, f"появились незаявленные файлы: {extra[:5]}"


def test_no_file_was_edited(manifest, root):
    changed = []
    for rel, pinned in manifest["files"].items():
        real = hashlib.sha256((root / rel).read_bytes()).hexdigest()
        if real != pinned:
            changed.append(rel)
    assert not changed, f"изменено без обновления манифеста: {changed[:5]}"


def test_the_combined_digest_matches_the_per_file_hashes(manifest):
    assert _digest(manifest["files"]) == manifest["digest"]


def test_mechanical_payload_is_still_present(manifest, root):
    """Регенерация, сохранившая файлы, но выпотрошившая activities, — регрессия."""
    with_activities = 0
    activities = 0
    for rel in manifest["files"]:
        record = json.loads((root / rel).read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("activities"):
            with_activities += 1
            activities += len(record["activities"])
    assert with_activities == 872
    assert activities == 1365


def test_nested_monster_activities_are_still_present(manifest, root):
    """У монстров activities лежат ВНУТРИ actions[].activities.

    Найдено критиком: счётчик выше видел только верхнеуровневые ключи и был
    слеп к 1350 вложенным активностям в 340 файлах. Регенерация, выпотрошившая
    способности монстров, прошла бы незамеченной."""
    nested = 0
    files = 0
    for rel in manifest["files"]:
        record = json.loads((root / rel).read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            continue
        found = sum(len(a["activities"]) for a in record.get("actions") or []
                    if isinstance(a, dict) and a.get("activities"))
        if found:
            files += 1
            nested += found
    assert files == 340
    assert nested == 1350


def test_the_corpus_carries_no_2014_text(manifest, root):
    """Проверка имён файлов мало что ловит — смотрим в само содержимое."""
    hits = []
    for rel in manifest["files"]:
        text = (root / rel).read_text(encoding="utf-8")
        if '"rules"' in text and '2014' in text:
            hits.append(rel)
    assert not hits, hits[:5]


def test_the_corpus_does_not_smuggle_2014_data(manifest):
    """Требование 10: не должно быть молчаливого смешения редакций."""
    polluted = [r for r in manifest["files"] if "2014" in r or "canonical_2014" in r]
    assert not polluted, polluted[:5]


def test_every_change_against_upstream_is_declared():
    """Пункт 10: корпус не должен расходиться с эталоном молча.

    `UPSTREAM.json` — эталон (1761 файл), `PATCHES.json` — правки кода,
    `CORPUS.json:corrections` — заявленные правки данных. Любой файл,
    отличающийся от эталона и не заявленный ни там, ни там, —
    незарегистрированная правка.

    Исключений больше нет. fireball.json держался в ALLOWED как
    «историческая незаявленная правка»; проверка показала, что байты файла
    побайтово совпадают с апстримом tapestria/nat20 e187c813, а расходится
    ОГЛАВЛЕНИЕ: отпечаток эталона для этого пути текущий апстрим не выдаёт
    ни для одного файла того же коммита. Пропуск не расширен, а закрыт —
    запись объявлена в CORPUS.json с причиной.
    """
    ALLOWED: set[str] = set()
    upstream = json.loads((VENDOR / "UPSTREAM.json").read_text(encoding="utf-8"))
    # PATCHES.json declares CODE patches only — `_reviewed` in
    # solo/nat20_content.py accepts nothing but `.py` there, so a corrected
    # corpus record is declared in CORPUS.json instead, with its reason.
    declared = {
        f["path"] for f in json.loads((VENDOR / "PATCHES.json").read_text(encoding="utf-8"))["files"]
    }
    corrections = json.loads((VENDOR / "CORPUS.json").read_text(encoding="utf-8")).get(
        "corrections", {}
    )
    for record in corrections.values():
        declared.add(record["path"])
        # A declared correction must state a reason and match the bytes on
        # disk: declaring a path must not become a way to smuggle in content.
        assert record.get("reason"), record["path"]
        assert record.get("sha256") == hashlib.sha256(
            (VENDOR / record["path"]).read_bytes()
        ).hexdigest(), record["path"]
    undeclared = []
    for rel, pinned in upstream["files"].items():
        path = VENDOR / rel
        if not path.exists():
            undeclared.append(("отсутствует", rel))
            continue
        if rel in declared:
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != pinned and rel not in ALLOWED:
            undeclared.append(("расходится", rel))
    assert not undeclared, undeclared[:5]

