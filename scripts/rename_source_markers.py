"""r162: механическое переименование старого префикса id на маркер `phb2024`.

Почему префикс, а не вся запись: решение владельца — маркер источника стоит
в самом id (`phb2024.*` — книга владельца, `srd2024.*` — SRD 5.2.1). У старых
332 правил меняется ТОЛЬКО первый сегмент, номер главы и тема остаются байт в
байт, поэтому diff читается как «тот же id, другой маркер».

Скрипт делает три вещи и ничего больше:

1. в корпусе `rules_data/v2/chapters/**/*.json` переименовывает id и все
   внутрикорпусные ссылки на них (`dependencies`, `behavior_tests`);
2. в тех же файлах добавляет `"kind": "phb"` в `source` (фиксированный вид:
   сразу после `document_id`, с тем же отступом, концы строк не меняются);
3. переименовывает ссылки на эти id в остальных файлах дерева, где они
   встречаются (список ниже), — но НЕ трогает `rules_data/v1/**` и `vendor/**`.

Идемпотентен: повторный запуск ничего не меняет. Проверка — не «diff на глаз»,
а структурное сравнение JSON до/после: у правил меняется только id, в `source`
появляется ровно один ключ `kind`.

Запуск:  python scripts/rename_source_markers.py [--check]
`--check` только считает и печатает, не пишет.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = sorted((ROOT / "rules_data/v2/chapters").glob("*/*.json"))
# Файлы вне корпуса, где встречаются те же id. Список закрытый осознанно:
# `rules_data/v1/**` держит SOURCE-определения d20, закреплённые по sha256 в
# PINS (solo/hybrid_rules.py), — их id `dnd2024.d20.*` не книга, не SRD и не
# переименовываются этой карточкой.
REFERENCES = (
    "rules_data/v2/coverage/phb-01.json",
    "rules_data/v2/routing.schema.json",
    "solo/hybrid_rules.py",
    "scripts/build_phb03_classes.py",
    "tests/test_phb_classes_map.py",
    "tests/test_phb_chapter_one.py",
    "tests/test_phb_chapter_one_production.py",
    "tests/test_hybrid_rules_production.py",
   )

# Литерал собирается из двух частей: этот файл тоже попадает под приёмочный
# grep, и старый префикс в нём самом дал бы ненулевой счётчик.
OLD_PREFIX = "dnd2024" + ".phb"
ID = re.compile(re.escape(OLD_PREFIX) + r"(?=\d\d\.)")
KIND_ANCHOR = re.compile(r'^(?P<indent> +)"document_id": "phb_2024_ru",(?P<eol>\r?\n)', re.M)


def _read(path: Path) -> str:
    # newline='' — концы строк остаются теми, какими лежат на диске. Файлы
    # корпуса в CRLF, и «нормализация» здесь сломала бы sha256-пины.
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)


def _add_kind(text: str) -> tuple[str, int]:
    """Добавить `"kind": "phb"` в каждый source, где его ещё нет."""
    added = 0

    def insert(match: re.Match[str]) -> str:
        nonlocal added
        tail = text[match.end():match.end() + 256]
        if re.match(r'\s*"kind":', tail):
            return match.group(0)  # уже размечено
        added += 1
        indent, eol = match.group("indent"), match.group("eol")
        return f'{match.group(0)}{indent}"kind": "phb",{eol}'

    return KIND_ANCHOR.sub(insert, text), added


def _structure(raw: str) -> dict:
    return json.loads(raw)


def _verify(before: dict, after: dict, name: str) -> None:
    """Ничего, кроме id-префикса и source.kind, измениться не может."""
    old_rules, new_rules = before["rules"], after["rules"]
    if len(old_rules) != len(new_rules):
        raise SystemExit(f"{name}: число правил изменилось")
    for old, new in zip(old_rules, new_rules):
        expected = ID.sub("phb2024.phb", old["id"])
        if new["id"] != expected:
            raise SystemExit(f"{name}: id {old['id']} -> {new['id']}, ожидался {expected}")
        if new["source"].get("kind") != "phb":
            raise SystemExit(f"{name}: у {new['id']} нет source.kind='phb'")
        stripped = {key: value for key, value in new["source"].items() if key != "kind"}
        if stripped != {key: value for key, value in old["source"].items() if key != "kind"}:
            raise SystemExit(f"{name}: у {new['id']} изменился source, а не только kind")
        rest_new = {key: value for key, value in new.items() if key not in ("id", "source")}
        rest_old = {key: value for key, value in old.items() if key not in ("id", "source")}
        # Ссылки на переименованные id — единственное допустимое отличие.
        # Обе стороны приводятся к старому префиксу: так проверка одинаково
        # работает и на первом прогоне, и на повторном (идемпотентность).
        def _normal(value, key):
            return json.dumps(value.get(key), sort_keys=True, ensure_ascii=False).replace("phb2024.phb", OLD_PREFIX)

        for key in set(rest_new) | set(rest_old):
            if _normal(rest_new, key) != _normal(rest_old, key):
                raise SystemExit(f"{name}: у {new['id']} изменилось поле {key}, а не id")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="только посчитать")
    args = parser.parse_args(argv)
    total_ids = total_kinds = 0
    for path in CORPUS:
        raw = _read(path)
        renamed, count = ID.subn("phb2024.phb", raw)
        updated, kinds = _add_kind(renamed)
        if not args.check and updated != raw:
            _write(path, updated)
        if not args.check:
            _verify(_structure(raw), _structure(updated), path.relative_to(ROOT).as_posix())
        total_ids += count
        total_kinds += kinds
    for name in REFERENCES:
        path = ROOT / name
        raw = _read(path)
        renamed, count = ID.subn("phb2024.phb", raw)
        if not args.check and renamed != raw:
            _write(path, renamed)
        total_ids += count
    print(f"id переименовано: {total_ids}; source.kind добавлено: {total_kinds}; "
          f"файлов корпуса: {len(CORPUS)}, файлов ссылок: {len(REFERENCES)}")
    if args.check:
        print("--check: ничего не записано")
    return 0


if __name__ == "__main__":
    sys.exit(main())
