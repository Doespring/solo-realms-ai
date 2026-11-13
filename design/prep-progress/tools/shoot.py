#!/usr/bin/env python3
"""r234 — съёмка и замеры полосы подготовки.

Поднимает локальный сервер над корнем проекта (иначе Chromium не отдаст
шрифты и не пустит в contentDocument кадра), запускает Chromium через
обёртку /home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome —
она сама подставляет недостающие библиотеки — и подключается к нему по CDP:
playwright эту сборку не запускает (у него свой, без libatk), а по протоколу
она работает.

Снимает четыре кадра — 375 и 1440 в обеих темах, dpr = 2 — в shots/ и меряет
то, что записано в SPEC.md. Замеры считает tools/measure.js. «Дыхание»
активной засечки на снимке остановлено: статичный кадр не должен показывать
случайную фазу.

Запуск: /home/hermes/.venvs/design/bin/python tools/shoot.py
"""
from __future__ import annotations

import functools
import http.server
import socket
import tempfile
import json
import os
import re
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent          # design/prep-progress
PROJECT = ROOT.parent.parent                            # корень проекта
TOOLS = Path(__file__).resolve().parent
OUT = ROOT / "shots"
CHECKS = TOOLS / "checks.json"
MEASURE = TOOLS / "measure.js"
CHROME = "/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome"
PROFILE = Path(tempfile.mkdtemp(prefix="chrome-prep-"))

FREEZE = (".prep-tick.is-active{animation:none!important;opacity:1!important}"
          ".prep-fill{transition:none!important}")


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve() -> tuple[str, socketserver.TCPServer]:
    handler = functools.partial(Quiet, directory=str(PROJECT))
    srv = Server(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_chrome(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
         "--force-color-profile=srgb", "--no-first-run", "--no-default-browser-check",
         f"--remote-debugging-port={port}", f"--user-data-dir={PROFILE}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=open(Path(tempfile.gettempdir()) / "chrome-prep.log", "w"))


def wait_cdp(port: int, proc: subprocess.Popen) -> None:
    for _ in range(160):
        if proc.poll() is not None:
            raise SystemExit(f"Chromium вышел: {proc.returncode}; см. {Path(tempfile.gettempdir()) / 'chrome-prep.log'}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
            return
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise SystemExit("Chromium не поднял CDP")


def shoot(browser, base: str, w: int, theme: str, name: str | None, freeze: bool = True,
          reduced: str | None = None) -> list[dict]:
    ctx = browser.new_context(viewport={"width": w, "height": 900}, device_scale_factor=2,
                              reduced_motion=reduced or "no-preference")
    page = ctx.new_page()
    page.goto(f"{base}/design/prep-progress/mock.html?w={w}&theme={theme}", wait_until="load", timeout=60000)
    page.wait_for_function("document.fonts.status === 'loaded'")
    page.wait_for_function("document.fonts.check('16px Book') && document.fonts.check('16px UI')")
    if freeze:
        page.add_style_tag(content=FREEZE)
    page.wait_for_timeout(250)
    data = page.evaluate(MEASURE.read_text(encoding="utf-8") + "\nwindow.__measurePrep()")
    if name:
        page.screenshot(path=str(OUT / name), full_page=True)
    ctx.close()
    for row in data:
        row.update(file=name or "reduced-motion", w=w, theme=theme)
    return data


def expect_ticks(valuetext: str) -> int:
    m = re.search(r"из\s+(\d+)", valuetext or "")
    total = int(m.group(1)) if m else 0
    unit = -(-total // 48) if total > 48 else 1
    return -(-total // unit) if total else 0


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    base, srv = serve()
    port = free_port()
    proc = start_chrome(port)
    results: list[dict] = []
    try:
        wait_cdp(port, proc)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            for w, theme in [(375, "dark"), (375, "light"), (1440, "dark"), (1440, "light")]:
                name = f"prep-{theme}-{w}.png"
                results += shoot(browser, base, w, theme, name)
                print(f"  {name}", flush=True)
            # prefers-reduced-motion: не снимок (он совпал бы с замороженным
            # кадром), а замер — вёрстка обязана остаться той же.
            calm = shoot(browser, base, 1440, "dark", None, freeze=False, reduced="reduce")
            print("  prefers-reduced-motion: замер без снимка", flush=True)
            # Витрина: четыре кадра в iframe — так макет смотрит человек.
            wall = browser.new_context(viewport={"width": 1600, "height": 1200},
                                       device_scale_factor=1)
            wp = wall.new_page()
            wp.goto(f"{base}/design/prep-progress/mock.html", wait_until="load", timeout=60000)
            wp.wait_for_function("document.fonts.status === 'loaded'")
            wp.wait_for_timeout(700)
            wp.screenshot(path=str(OUT / "wall.png"), full_page=True)
            wall.close()
            print("  wall.png", flush=True)
            browser.close()
    finally:
        proc.terminate()
        srv.shutdown()

    CHECKS.write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    bad: list[str] = []
    cap_h: dict[tuple[int, str], set] = {}
    for r in results:
        where = f'{r["file"]} · {r["scene"]}'
        if r["hscroll"] > 0:
            bad.append(f'{where}: горизонтальная прокрутка {r["hscroll"]} px')
        if r["outside"]:
            bad.append(f'{where}: за краем панели {r["outside"][:2]}')
        if r["low"]:
            bad.append(f'{where}: текст мельче 14 px или контраст ниже 4.5 — {r["low"][:2]}')
        if abs(r["bar"]["got"] - r["bar"]["want"]) > 0.6:
            bad.append(f'{where}: заливка {r["bar"]["got"]} % вместо {r["bar"]["want"]} %')
        want = expect_ticks(r["bar"]["valuetext"])
        if r["ticks"]["n"] != want:
            bad.append(f'{where}: засечек {r["ticks"]["n"]}, ожидалось {want}')
        failed = int(r["failed"])
        if r["alert"]["hidden"] and failed:
            bad.append(f'{where}: штамп сбоя спрятан')
        if not r["alert"]["hidden"] and not failed:
            bad.append(f'{where}: штамп виден без сбоя')
        if not r["alert"]["hidden"] and r["alert"]["cr"] is not None and r["alert"]["cr"] < 4.5:
            bad.append(f'{where}: контраст штампа {r["alert"]["cr"]}')
        # Готово: строку фразы панель убирает намеренно — высота 0 там законна.
        if r["caption"] is not None and r["running"]:
            cap_h.setdefault((r["w"], r["theme"]), set()).add(r["caption"]["h"])

    for key, heights in cap_h.items():
        if len(heights) > 1:
            bad.append(f"поле фразы разной высоты при {key}: {sorted(heights)}")

    # Уменьшенное движение не меняет геометрию: сверяем с обычным кадром.
    plain = {r["scene"]: r for r in results if r["w"] == 1440 and r["theme"] == "dark"}
    for r in calm:
        p0 = plain.get(r["scene"])
        if not p0:
            continue
        if abs(p0["panel"]["h"] - r["panel"]["h"]) > 0.6:
            bad.append(f'reduced-motion · {r["scene"]}: высота панели {r["panel"]["h"]} '
                       f'вместо {p0["panel"]["h"]}')
        # В reduce measure не отдаёт поле фразы (оно не анимируется) — сравниваем,
        # только когда оба замера его вернули.
        if p0["caption"] and r["caption"] and abs(p0["caption"]["h"] - r["caption"]["h"]) > 0.6:
            bad.append(f'reduced-motion · {r["scene"]}: поле фразы {r["caption"]["h"]} '
                       f'вместо {p0["caption"]["h"]}')

    print("\nпроверки: " + ("OK" if not bad else f"{len(bad)} замечаний"))
    for b in bad:
        print("  ! " + b)

    print("\n| сцена | тема | ширина | панель | кегль min | контраст min | заливка | засечки всего/г/сбой/сейчас/ожид. | штамп |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        t = r["ticks"]
        print(f'| {r["scene"]} | {r["theme"]} | {r["w"]} | {r["panel"]["w"]}×{r["panel"]["h"]} | {r["textMin"]} | '
              f'{r["crMin"]} | {r["bar"]["got"]}/{r["bar"]["want"]} % | {t["n"]}: '
              f'{t["done"]}/{t["failed"]}/{t["active"]}/{t["pending"]} | '
              f'{"да" if not r["alert"]["hidden"] else "—"} |')

    print("\nвысота документа по ширине (для кадров витрины): "
          + ", ".join(f"{k}: {sorted(v)}" for k, v in sorted({str(r['w']): {r2['docH'] for r2 in results if r2['w'] == r['w']} for r in results}.items())))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
