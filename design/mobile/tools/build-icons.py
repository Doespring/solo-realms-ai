#!/usr/bin/env python3
"""Собирает design/mobile/icons.css из web/visual/icons/*.svg.

Иконки — game-icons.net, CC BY 3.0 (Lorc, Delapouite), те же файлы, что в
приложении. В CSS каждая иконка — класс .ic-<имя> с data:URI, иначе маска
не подгружается по file:// (CORS): макет должен открываться с диска без
сервера. Это не копия файлов: пути читаются из web/visual/icons.

    /home/hermes/.venvs/design/bin/python tools/build-icons.py
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # design/mobile
ICONS = ROOT.parent.parent / "web" / "visual" / "icons"
OUT = ROOT / "icons.css"

# Только то, что стоит на экранах r231. Новую — добавить в список и в
# приложение: набор должен совпадать, иначе макет обещает несуществующую иконку.
WANTED = ["compass", "person", "journal", "book", "save", "settings",
          "heart", "shield", "feather", "exit", "map", "coins",
          # r231, раунд 2: пункты шторки меню. Те же файлы, что в приложении
          # (web/visual/icons), чтобы макет не обещал несуществующую иконку.
          "skills", "inventory", "chest", "chronicle", "wizard", "torch"]

HEAD = """/* Иконки макетов r231: game-icons.net (CC BY 3.0), файлы из web/visual/icons.
   Сгенерировано tools/build-icons.py — не править вручную.
   data:URI, потому что маска с внешним URL по file:// не грузится.
   Цвет — currentColor, размер задаёт .icon в mobile.css. */
"""

parts = [HEAD]
for name in WANTED:
    path = ICONS / f"{name}.svg"
    if not path.exists():
        raise SystemExit(f"нет файла иконки: {path}")
    svg = path.read_text(encoding="utf-8").strip().replace("currentColor", "#fff")
    enc = urllib.parse.quote(svg, safe=" ~!@$*()_=+-/:;,.'")
    parts.append(f'.ic-{name}{{--icon:url("data:image/svg+xml,{enc}")}}')

OUT.write_text("\n".join(parts) + "\n", encoding="utf-8")
print(f"иконок: {len(WANTED)} → {OUT.relative_to(ROOT.parent.parent)}, "
      f"{OUT.stat().st_size // 1024} КБ")
