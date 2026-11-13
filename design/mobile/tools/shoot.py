#!/usr/bin/env python3
"""r231 — съёмка и замеры мобильных макетов.

Открывает design/mobile/index.html в настоящем Chromium (Playwright),
снимает каждый кадр .phone целиком (375×812 и 390×844, обе темы, dpr=2)
в design/mobile/shots/ и меряет то, что записано в SPEC.md.

Замеры считает tools/measure.js — один файл на оба пути съёмки (Playwright
и CDP). В этом образе Playwright не стартует (нет libatk/libX), поэтому
числа из tools/checks.json сняты тем же Chromium по CDP с тем же
measure.js: две реализации не разъезжаются.

Проверки:
  • размер кадра совпадает с заявленным (375×812 / 390×844);
  • шапка [data-zone=bar] не выше 64;
  • нет горизонтальной прокрутки и выхода за края;
  • зоны нажатия не меньше 44×44;
  • ЛЮБОЙ видимый текст ≥ 14, основной (рассказ, поле, строки) ≥ 16;
  • главное действие главного экрана видно без скролла;
  • ритм отступов: gap/padding/margin только из шкалы SPEC §2;
  • шторка: высота, прокрутка внутри, вкладки, закреплённый «Выход»;
  • сколько пикселей остаётся ленте при открытой клавиатуре.

Результат: shots/<экран>-<тема>-<ширина>.png, tools/checks.json и
таблица в stdout. Запуск: /home/hermes/.venvs/design/bin/python tools/shoot.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent          # design/mobile
OUT = ROOT / "shots"
CHECKS = Path(__file__).resolve().parent / "checks.json"
MEASURE = Path(__file__).resolve().parent / "measure.js"
URL = (ROOT / "index.html").as_uri()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    measure_src = MEASURE.read_text(encoding="utf-8")
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1800, "height": 1200},
                                device_scale_factor=2)
        page.goto(URL)
        page.wait_for_function("document.fonts.status === 'loaded'")
        page.add_script_tag(content=measure_src)
        page.wait_for_timeout(250)

        frames = page.query_selector_all(".phone")
        print(f"кадров на витрине: {len(frames)}", flush=True)

        for phone in frames:
            screen = phone.get_attribute("data-screen")
            theme = phone.get_attribute("data-theme")
            w = phone.get_attribute("data-w")
            sel = f'.phone[data-screen="{screen}"][data-theme="{theme}"][data-w="{w}"]'
            # лента игры встаёт на последнее сообщение (SPEC §8.3) — до замера и снимка
            page.evaluate("(s) => window.__prepFrame(s)", sel)
            page.wait_for_timeout(30)
            m = page.evaluate("(s) => window.__measureFrame(s)", sel)
            name = f"{screen}-{theme}-{w}.png"
            phone.scroll_into_view_if_needed()
            page.wait_for_timeout(40)
            phone.screenshot(path=str(OUT / name))
            m.update(screen=screen, theme=theme, w=int(w), file=name)
            results.append(m)
            print(f"  {name:<32} bar={m['bar']} body={m['body']['h'] if m['body'] else '-'} "
                  f"dock={m['dock']} tap_bad={len(m['tapBad'])} out={len(m['outside'])}",
                  flush=True)

        browser.close()

    CHECKS.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # ── Сводка ────────────────────────────────────────────────────────────────
    bad = []
    for r in results:
        want, want_h = (390, 844) if r["w"] == 390 else (375, 812)
        if round(r["frame"]["w"]) != want or round(r["frame"]["h"]) != want_h:
            bad.append(f'{r["file"]}: кадр {r["frame"]} вместо {want}×{want_h}')
        if r["hscroll"] > 0:
            bad.append(f'{r["file"]}: гориз. прокрутка {r["hscroll"]} px')
        if r["bar"] and r["bar"] > 64:
            bad.append(f'{r["file"]}: шапка {r["bar"]} > 64')
        if r["outside"]:
            bad.append(f'{r["file"]}: за краем {len(r["outside"])} элем.: {r["outside"][:2]}')
        if r["tapBad"]:
            bad.append(f'{r["file"]}: нажатие < 44: {r["tapBad"][:3]}')
        if r["textBad"]:
            bad.append(f'{r["file"]}: текст < 14: {r["textBad"][:3]}')
        if r["mainBad"]:
            bad.append(f'{r["file"]}: основной текст < 16: {r["mainBad"][:3]}')
        if r["screen"] == "home" and r["primary"]:
            if r["primary"]["bottom"] > r["frame"]["h"] - 34:
                bad.append(f'{r["file"]}: главное действие ниже сгиба ({r["primary"]["bottom"]})')
        if r["rhythmBad"]:
            bad.append(f'{r["file"]}: отступы вне шкалы {r["rhythmBad"]}')
        if r["sheet"] and r["sheet"]["scroll"] > 0:
            bad.append(f'{r["file"]}: шторка прокручивается на {r["sheet"]["scroll"]} px')

    print("\nпроверки: " + ("OK" if not bad else f"{len(bad)} замечаний"))
    for b in bad:
        print("  ! " + b)

    print("\n| экран | тема | кадр | шапка | тело | док | текст min | осн. min | шторка | нажатий |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        sh = "—"
        if r["sheet"]:
            sh = f'{r["sheet"]["h"]:.0f} ({r["sheet"]["pane"]}), прокр. {r["sheet"]["scroll"]}'
        print(f'| {r["screen"]} | {r["theme"]} | {r["w"]}×{r["frame"]["h"]:.0f} | {r["bar"]} | '
              f'{r["body"]["h"] if r["body"] else "—"} | {r["dock"] or "—"} | {r["textMin"]} | '
              f'{r["mainMin"]} | {sh} | {r["tapN"]} |')
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
