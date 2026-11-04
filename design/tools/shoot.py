#!/usr/bin/env python3
"""Скриншоты и проверки макетов Solo Realms AI.

Запуск (нужен playwright и локальный chromium):
    python3 -m pip install playwright
    python3 tools/shoot.py

Что делает:
  • открывает каждый макет в headless Chromium при 375 и 1440 px;
  • пишет shots/<имя>-375.png и shots/<имя>-1440.png;
  • проверяет, что нет горизонтальной прокрутки, ошибок консоли,
    ошибок страницы и неудачных запросов;
  • печатает отчёт и завершается кодом 1, если что-то не так.

Путь к браузеру можно задать переменной CHROME_PATH. На этой машине
использован уже скачанный Chromium Playwright и локальные библиотеки:

    export LD_LIBRARY_PATH=$HOME/.local/chromium-libs/root/usr/lib/x86_64-linux-gnu
    export CHROME_PATH=$HOME/.cache/ms-playwright/chromium_headless_shell-1248/\
chrome-headless-shell-linux64/chrome-headless-shell
    python3 tools/shoot.py
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SCREENS = ROOT / "screens"
SHOTS = ROOT / "shots"

PAGES = ["01-start", "02-character", "03-play", "04-combat", "05-book"]
WIDTHS = [(375, 812), (1440, 900)]

CHROME = os.environ.get(
    "CHROME_PATH",
    "/home/hermes/.cache/ms-playwright/chromium_headless_shell-1248/"
    "chrome-headless-shell-linux64/chrome-headless-shell",
)


def main() -> int:
    SHOTS.mkdir(exist_ok=True)
    problems = []
    report = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME, args=["--no-sandbox", "--font-render-hinting=none"]
        )
        for name in PAGES:
            for width, height in WIDTHS:
                page = browser.new_page(viewport={"width": width, "height": height},
                                        device_scale_factor=1)
                events = []
                page.on("console", lambda m: events.append(("console:" + m.type, m.text))
                        if m.type in ("error", "warning") else None)
                page.on("pageerror", lambda e: events.append(("pageerror", str(e))))
                page.on("requestfailed", lambda r: events.append(("requestfailed", r.url)))

                url = (SCREENS / f"{name}.html").as_uri()
                page.goto(url, wait_until="load")
                page.wait_for_timeout(400)

                metrics = page.evaluate(
                    """() => ({
                        scrollW: document.documentElement.scrollWidth,
                        clientW: document.documentElement.clientWidth,
                        scrollH: document.documentElement.scrollHeight,
                        bodyFont: getComputedStyle(document.body).fontFamily,
                        storyFont: (() => {
                            const el = document.querySelector('.prose p');
                            return el ? getComputedStyle(el).fontSize + ' / ' + getComputedStyle(el).lineHeight : null;
                        })(),
                        fontsOk: document.fonts.check('20px "Book"') && document.fonts.check('14px "UI"'),
                        icons: [...document.querySelectorAll('.icon')].filter(e => {
                            const s = getComputedStyle(e);
                            return !(s.maskImage || s.webkitMaskImage || 'none').includes('data:image');
                        }).length,
                        hOverflow: [...document.querySelectorAll('body *')].filter(e => {
                            const r = e.getBoundingClientRect();
                            return r.width > 0 && r.right > document.documentElement.clientWidth + 1;
                        }).slice(0, 5).map(e => e.tagName + '.' + (e.className || ''))
                    })"""
                )

                shot = SHOTS / f"{name}-{width}.png"
                page.screenshot(path=str(shot), full_page=True)
                page.close()

                line = (f"{name} @{width}: {metrics['scrollW']}x{metrics['scrollH']} "
                        f"fonts={metrics['fontsOk']} story={metrics['storyFont']} "
                        f"badIcons={metrics['icons']}")
                report.append(line)

                if metrics["scrollW"] > metrics["clientW"]:
                    problems.append(f"{name} @{width}: горизонтальная прокрутка "
                                    f"{metrics['scrollW']} > {metrics['clientW']}; "
                                    f"виновники: {metrics['hOverflow']}")
                if not metrics["fontsOk"]:
                    problems.append(f"{name} @{width}: шрифты не загрузились")
                if metrics["icons"]:
                    problems.append(f"{name} @{width}: иконок без маски {metrics['icons']}")
                for kind, text in events:
                    problems.append(f"{name} @{width}: {kind}: {text[:160]}")

        browser.close()

    print("\n".join(report))
    print()
    if problems:
        print("ПРОБЛЕМЫ:")
        print("\n".join("  • " + x for x in problems))
        return 1
    print("OK: прокрутки нет, консоль чистая, шрифты и иконки на месте.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
