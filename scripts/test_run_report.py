#!/usr/bin/env python3
"""Сводка по JSONL от batch_test_run.py: итоговые числа и имена всех падений.

    .venv/bin/python scripts/test_run_report.py reports/TEST-RUN-r139.jsonl

Печатает:
- сколько пакетов записано из скольких файлов, сколько пакетов без итоговой
  строки (OOM/таймаут — это НЕ «0 падений»);
- суммы passed/failed/errors/skipped;
- все FAILED/ERROR по файлам, отсортированные, без повторов;
- отдельно список файлов, чей пакет был прерван (цифр по ним нет).
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "reports/TEST-RUN.jsonl")
    if not path.is_file():
        print(f"нет файла {path}", file=sys.stderr)
        return 2

    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    tot = Counter()
    tested_files: set[str] = set()
    interrupted: list[str] = []
    failures: dict[str, list[str]] = defaultdict(list)
    unfinalised: list[int] = []

    for rec in records:
        tested_files.update(rec.get("files", []))
        if rec.get("timeout") or not rec.get("summary_line"):
            unfinalised.append(rec.get("batch"))
            interrupted.extend(rec.get("files", []))
        for key in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed"):
            tot[key] += rec.get(key, 0)
        for name in rec.get("failed_names", []) + rec.get("error_names", []):
            file_part = name.split("::")[0]
            failures[file_part].append(name)

    print(f"файл сводки: {path}")
    print(f"пакетов в файле: {len(records)}")
    print(f"файлов тестов затронуто: {len(tested_files)}")
    print(
        "ИТОГО: "
        f"{tot['passed']} passed, {tot['failed']} failed, {tot['errors']} errors, "
        f"{tot['skipped']} skipped, {tot['xfailed']} xfailed, {tot['xpassed']} xpassed"
    )
    total_fail_names = sum(len(v) for v in failures.values())
    print(f"имён в FAILED/ERROR: {total_fail_names}")

    if unfinalised:
        print(f"\nПАКЕТЫ БЕЗ ИТОГОВОЙ СТРОКИ (таймаут/обрыв, цифр нет): {unfinalised}")
        for name in sorted(set(interrupted)):
            print(f"  ! {name}")

    if failures:
        print("\nПАДЕНИЯ ПО ФАЙЛАМ:")
        for file_part in sorted(failures):
            names = sorted(set(failures[file_part]))
            print(f"\n{file_part} — {len(names)}")
            for name in names:
                print(f"  - {name}")
    else:
        print("\nпадений нет")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
