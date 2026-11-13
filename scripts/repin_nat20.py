"""Перепин `vendor/nat20/PATCHES.json` под текущие байты файлов.

Зачем: `solo/nat20_content.py::_reviewed` строит ожидаемую карту SHA как
UPSTREAM + записи PATCHES.json, и `verify_tree` отказывается работать, если
хоть один `.py` в установленном дереве не совпадает. Правка движка без
перепина = `ValueError: Content differs from pinned SHA: <файл>`.

Скрипт не «обновляет всё подряд»: он принимает явный список путей, считает
их SHA и либо правит существующую запись (`modified`), либо добавляет новую.
Любой путь вне пиннутого дерева — ошибка, а не молчаливое расширение пина.

    python scripts/repin_nat20.py packages/dnd5e-engine/src/dnd5e_engine/check.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "nat20"
PATCHES = VENDOR / "PATCHES.json"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: repin_nat20.py <path-relative-to-vendor/nat20> [...]")
        return 2

    lock = json.loads((VENDOR / "UPSTREAM.json").read_text(encoding="utf-8"))
    patches = json.loads(PATCHES.read_text(encoding="utf-8"))
    upstream_files = set(lock["files"])
    by_path = {entry["path"]: entry for entry in patches["files"]}

    changed = []
    for rel in argv:
        target = VENDOR / rel
        if not target.is_file():
            print(f"ERROR: no such file: {target}", file=sys.stderr)
            return 1
        if not rel.endswith(".py"):
            # `_reviewed` accepts nothing but `.py`; corpus records go to CORPUS.json.
            print(f"ERROR: PATCHES.json declares code patches only: {rel}", file=sys.stderr)
            return 1
        digest = sha256_of(target)
        entry = by_path.get(rel)
        if entry is None:
            if rel not in upstream_files:
                print(f"ERROR: {rel} is outside the pinned upstream tree", file=sys.stderr)
                return 1
            entry = {"path": rel, "kind": "modified", "sha256": digest}
            patches["files"].append(entry)
            by_path[rel] = entry
            changed.append(("added", rel, digest))
        else:
            before = entry.get("sha256")
            if before == digest:
                changed.append(("unchanged", rel, digest))
                continue
            entry["sha256"] = digest
            changed.append(("repinned", rel, digest))

    patches["files"].sort(key=lambda e: e["path"])
    PATCHES.write_text(
        json.dumps(patches, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for action, rel, digest in changed:
        print(f"{action:9} {digest[:16]}  {rel}")
    print(f"PATCHES.json: {len(patches['files'])} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
