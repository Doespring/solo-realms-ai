#!/usr/bin/env python3
"""Собирает assets/icons.css из assets/icons/*.svg.

Иконки — game-icons.net, CC BY 3.0 (Lorc, Delapouite). В CSS каждая иконка
становится классом .ic-<имя> с переменной --icon (data:URI), чтобы маска
работала и по file:// без CORS-ошибок в консоли.

Запускать после добавления нового SVG:
    python3 tools/build-icons.py
"""
import glob
import os
import urllib.parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(HERE)

parts = ["/* Иконки game-icons.net (CC BY 3.0). Сгенерировано tools/build-icons.py — не править вручную. */\n"]
for path in sorted(glob.glob("assets/icons/*.svg")):
    name = os.path.basename(path)[:-4]
    svg = open(path, encoding="utf-8").read().strip().replace("currentColor", "#fff")
    enc = urllib.parse.quote(svg, safe=" ~!@$*()_=+-/:;,.'")
    parts.append(f'.ic-{name}{{--icon:url("data:image/svg+xml,{enc}")}}')

open("assets/icons.css", "w", encoding="utf-8").write("\n".join(parts) + "\n")
print("иконок:", len(glob.glob("assets/icons/*.svg")))
