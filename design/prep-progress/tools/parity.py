#!/usr/bin/env python3
"""r234 — сверка: код из HANDOFF (tools/app-panel.js) против макета mock.html.

Одна и та же сцена, одна и та же ширина и тема, один и тот же CSS — две
страницы: макет (данные вписаны руками) и панель, которую рисует предложенный
код на данных `run.progress`. Скрипт снимает по каждому листу: высоту, классы
состояния, заголовок, надзаголовок, часы, заливку, засечки, число фразы,
числа и плашку — и сравнивает.

Если расхождение есть, значит HANDOFF описывает не то, что нарисовано в
макете, и кодер внедрит другую панель. Поэтому сверка — часть приёмки.

Запуск: /home/hermes/.venvs/design/bin/python tools/parity.py
"""
from __future__ import annotations

import functools
import http.server
import json
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PROJECT = ROOT.parent.parent
CHROME = "/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome"
PROFILE = Path(tempfile.mkdtemp(prefix="chrome-parity-"))

EXTRACT = r"""
() => {
  const out = [];
  for (const scene of document.querySelectorAll('.scene')) {
    const panel = scene.querySelector('.prep-panel');
    if (!panel) { out.push({scene: scene.dataset.scene, missing: true}); continue; }
    const r = panel.getBoundingClientRect();
    const bar = panel.querySelector('.prep-bar');
    const ticks = Array.from(panel.querySelectorAll('.prep-tick'));
    const tally = {done: 0, failed: 0, active: 0, pending: 0};
    for (const t of ticks) {
      if (t.classList.contains('is-failed')) tally.failed++;
      else if (t.classList.contains('is-active')) tally.active++;
      else if (t.classList.contains('is-done')) tally.done++;
      else tally.pending++;
    }
    const cap = panel.querySelector('.prep-caption');
    const alert = panel.querySelector('.prep-alert');
    const txt = el => el ? el.textContent.replace(/\s+/g, ' ').trim() : '';
    out.push({
      scene: scene.dataset.scene,
      h: +r.height.toFixed(1),
      w: +r.width.toFixed(1),
      classes: ['is-playable', 'is-finished', 'is-failed', 'is-stopped']
        .filter(c => panel.classList.contains(c)).join(' '),
      title: txt(panel.querySelector('.prep-title')),
      eyebrow: txt(panel.querySelector('.prep-eyebrow')),
      clock: txt(panel.querySelector('.prep-clock')),
      steps: Array.from(panel.querySelectorAll('.prep-step')).map(s => s.textContent +
        (s.classList.contains('is-current') ? '*' : '') +
        (s.classList.contains('is-done') ? '+' : '')).join(' '),
      percent: Number(bar.getAttribute('aria-valuenow')),
      valuetext: bar.getAttribute('aria-valuetext'),
      ticks: {n: ticks.length, ...tally},
      captionH: cap ? +cap.getBoundingClientRect().height.toFixed(1) : 0,
      meta: txt(panel.querySelector('.prep-meta')),
      alert: {hidden: alert.hasAttribute('hidden'), text: txt(alert)},
      spine: getComputedStyle(panel).borderLeftColor,
    });
  }
  return out;
}
"""


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
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
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_cdp(port: int, proc: subprocess.Popen) -> None:
    for _ in range(160):
        if proc.poll() is not None:
            raise SystemExit("Chromium вышел")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2).read()
            return
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise SystemExit("Chromium не поднял CDP")


def grab(browser, url: str, w: int) -> list[dict]:
    ctx = browser.new_context(viewport={"width": w, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto(url, wait_until="load", timeout=60000)
    page.wait_for_function("document.fonts.status === 'loaded'")
    page.wait_for_function("document.fonts.check('16px Book') && document.fonts.check('16px UI')")
    page.wait_for_timeout(300)
    data = page.evaluate(EXTRACT)
    ctx.close()
    return data


def main() -> int:
    base, srv = serve()
    port = free_port()
    proc = start_chrome(port)
    problems: list[str] = []
    rows: list[str] = []
    try:
        wait_cdp(port, proc)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            for w in (375, 1440):
                for theme in ("dark", "light"):
                    mock = {r["scene"]: r for r in
                            grab(browser, f"{base}/design/prep-progress/mock.html?w={w}&theme={theme}", w)}
                    code = {r["scene"]: r for r in
                            grab(browser, f"{base}/design/prep-progress/tools/parity.html?theme={theme}", w)}
                    for scene, a in mock.items():
                        b = code.get(scene)
                        where = f"{w} · {theme} · {scene}"
                        if not b or b.get("missing"):
                            problems.append(f"{where}: панель не построена")
                            continue
                        for key in ("classes", "title", "eyebrow", "clock", "steps",
                                    "percent", "valuetext", "meta"):
                            if a[key] != b[key]:
                                problems.append(f"{where}: {key} = {b[key]!r}, в макете {a[key]!r}")
                        if a["ticks"] != b["ticks"]:
                            problems.append(f"{where}: засечки {b['ticks']}, в макете {a['ticks']}")
                        if a["alert"] != b["alert"]:
                            problems.append(f"{where}: плашка {b['alert']}, в макете {a['alert']}")
                        if a["spine"] != b["spine"]:
                            problems.append(f"{where}: корешок {b['spine']}, в макете {a['spine']}")
                        if abs(a["h"] - b["h"]) > 0.6:
                            problems.append(f"{where}: высота {b['h']}, в макете {a['h']}")
                        if abs(a["w"] - b["w"]) > 0.6:
                            problems.append(f"{where}: ширина {b['w']}, в макете {a['w']}")
                        # Поле фразы: в макете и в коде фраза разная (ротатор берёт
                        # первую строку), но высота поля обязана совпасть.
                        if abs(a["captionH"] - b["captionH"]) > 0.6:
                            problems.append(f"{where}: поле фразы {b['captionH']}, в макете {a['captionH']}")
                        rows.append(f'| {w} | {theme} | {scene} | {a["h"]} / {b["h"]} | '
                                    f'{a["percent"]}/{b["percent"]} | {a["ticks"]["n"]} | '
                                    f'{"да" if not b["alert"]["hidden"] else "—"} |')
            browser.close()
    finally:
        proc.terminate()
        srv.shutdown()

    print("| ширина | тема | сцена | высота макет/код | заливка макет/код | засечек | плашка |")
    print("|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    print("\nсверка: " + ("OK" if not problems else f"{len(problems)} расхождений"))
    for q in problems:
        print("  ! " + q)
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
