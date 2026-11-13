#!/usr/bin/env python3
"""Сводный прогон tests/ пакетами по N файлов с дописыванием результата в JSONL.

Зачем: VPS 3.9 ГБ, полный прогон 300+ файлов умирает по памяти без итоговой
строки, и отличить OOM от падения тестов невозможно. Пакет за пакетом —
единственный способ получить честные цифры и продолжить после обрыва.

Запуск:
    .venv/bin/python scripts/batch_test_run.py --root . --out reports/TEST-RUN-r139.jsonl

Свойства:
- один прогон за раз (никакого параллелизма), между пакетами проверка памяти;
- JSONL дописывается после каждого пакета, повторный запуск продолжает с места;
- падение СБОРА не обрывает прогон (--continue-on-collection-errors);
- таймаут на пакет: пакет помечается timeout и не выдаёт ложных «0 passed»;
- имена FAILED/ERROR сохраняются как есть (вместе с путём файла).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SUMMARY_RE = re.compile(
    r"(?:(?P<failed>\d+) failed)?[,\s]*"
    r"(?:(?P<errors>\d+) errors?)?[,\s]*"
    r"(?:(?P<passed>\d+) passed)?[,\s]*"
    r"(?:(?P<skipped>\d+) skipped)?[,\s]*"
    r"(?:(?P<xfailed>\d+) xfailed)?[,\s]*"
    r"(?:(?P<xpassed>\d+) xpassed)?"
    r".*?in (?P<dur>[0-9.]+)s",
)


def parse_counts(output: str) -> dict:
    """Числа из последней итоговой строки pytest."""
    counts = {
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "duration": None,
    }
    tail = [ln for ln in output.splitlines() if re.search(r" in [0-9.]+s", ln)]
    for line in reversed(tail):
        m = SUMMARY_RE.search(line)
        if not m:
            continue
        for key in ("failed", "errors", "passed", "skipped", "xfailed", "xpassed"):
            counts[key] = int(m.group(key) or 0)
        counts["duration"] = float(m.group("dur"))
        return counts
    return counts


def collect_names(output: str, kind: str) -> list[str]:
    """`FAILED test_x.py::test_y - reason` -> ['test_x.py::test_y', ...]."""
    out = []
    for line in output.splitlines():
        if line.startswith(kind + " "):
            name = line[len(kind) + 1 :].split(" - ")[0].strip()
            if name:
                out.append(name)
    return out


def mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 10_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="reports/TEST-RUN.jsonl")
    ap.add_argument("--batch-size", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=420, help="секунд на пакет")
    ap.add_argument("--min-mem", type=int, default=400, help="МБ, ниже — пауза")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    out_path = root / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(
        p.relative_to(root).as_posix() for p in root.glob("tests/test_*.py")
    )
    batches = [files[i : i + args.batch_size] for i in range(0, len(files), args.batch_size)]

    done = set()
    if out_path.is_file():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add(rec.get("batch"))

    py = root / ".venv" / "bin" / "python"
    if not py.is_file():
        py = Path(sys.executable)

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    print(f"файлов: {len(files)}, пакетов: {len(batches)}, готово: {len(done)}", flush=True)

    with out_path.open("a", encoding="utf-8") as fh:
        for idx, batch in enumerate(batches, start=1):
            if idx in done:
                continue
            for _ in range(60):
                avail = mem_available_mb()
                if avail >= args.min_mem:
                    break
                print(f"память {avail} МБ < {args.min_mem}, ждём 10 с", flush=True)
                time.sleep(10)

            cmd = [
                str(py),
                "-m",
                "pytest",
                *batch,
                "-q",
                "--no-header",
                "--tb=no",
                "-p",
                "no:cacheprovider",
                "--continue-on-collection-errors",
            ]
            started = time.time()
            timeout_hit = False
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=args.timeout,
                    env=env,
                )
                output = (proc.stdout or "") + (proc.stderr or "")
                rc = proc.returncode
            except subprocess.TimeoutExpired as exc:
                timeout_hit = True
                rc = -9
                raw = exc.stdout or b""
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                output = raw

            counts = parse_counts(output)
            rec = {
                "batch": idx,
                "files": batch,
                "returncode": rc,
                "timeout": timeout_hit,
                "wall": round(time.time() - started, 2),
                "passed": counts["passed"],
                "failed": counts["failed"],
                "errors": counts["errors"],
                "skipped": counts["skipped"],
                "xfailed": counts["xfailed"],
                "xpassed": counts["xpassed"],
                "duration": counts["duration"],
                "failed_names": collect_names(output, "FAILED"),
                "error_names": collect_names(output, "ERROR"),
                "summary_line": "\n".join(
                    ln for ln in output.splitlines() if re.search(r" in [0-9.]+s", ln)
                )[-300:],
            }
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            flag = "TIMEOUT" if timeout_hit else ("OK" if rc == 0 else f"rc={rc}")
            print(
                f"[{idx}/{len(batches)}] {flag} "
                f"p={counts['passed']} f={counts['failed']} e={counts['errors']} "
                f"s={counts['skipped']} {rec['wall']}s",
                flush=True,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
