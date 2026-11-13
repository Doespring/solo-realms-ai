"""r235: обмер живого каркаса через Playwright (без браузерного агента).

    .venv/bin/python scripts/probe_r235_frame.py dump [ширина]
    .venv/bin/python scripts/probe_r235_frame.py shots

Порт и профиль — свои (19235, runtime/r235-live), стенд поднимает serve_r235.py.
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:19235/'
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports' / 'r235'
CHROME = Path.home() / '.cache' / 'ms-playwright' / 'chromium-1248' / 'chrome-linux64' / 'chrome'


def rect(page, selector):
    return page.evaluate(
        """s=>{const e=document.querySelector(s);if(!e)return null;
        const b=e.getBoundingClientRect(),c=getComputedStyle(e);
        return {x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height),
        display:c.display,font:c.fontSize,pad:c.padding}}""",
        selector,
    )


def nav_items(page):
    return page.evaluate(
        """()=>[...document.querySelectorAll('[data-view]')].map(b=>({
        view:b.dataset.view,text:b.innerText.replace(/\\s+/g,' ').trim(),
        inSidebar:!!b.closest('.sidebar'),hidden:!!b.offsetParent===false,
        w:Math.round(b.getBoundingClientRect().width),h:Math.round(b.getBoundingClientRect().height)}))"""
    )


def fonts(page):
    return page.evaluate(
        """()=>{const out={};for(const e of document.querySelectorAll('body *')){
        if(!e.offsetParent||e.closest('[hidden]'))continue;
        const t=(e.textContent||'').trim();if(!t||e.children.length&&!e.childNodes.length)continue;
        if(![...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()))continue;
        const s=getComputedStyle(e).fontSize;out[s]=(out[s]||0)+1}return out}"""
    )


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'dump'
    widths = [int(x) for x in sys.argv[2].split(',')] if len(sys.argv) > 2 else [1440]
    OUT.mkdir(parents=True, exist_ok=True)
    result = {}
    with sync_playwright() as p:
        # Свой headless-хром из ~/.cache не стартует (нет libatk и др.);
        # берём тот, что уже поднят на 9333 (у него свои библиотеки).
        browser = p.chromium.connect_over_cdp('http://127.0.0.1:9333')
        for width in widths:
            for theme in ('dark', 'light'):
                context = browser.new_context(viewport={'width': width, 'height': 900})
                page = context.new_page()
                page.goto(BASE)
                page.evaluate("t=>{localStorage.setItem('solo-theme',t)}", theme)
                page.reload()
                page.wait_for_timeout(900)
                key = f'{width}-{theme}'
                if mode == 'shots':
                    page.screenshot(path=str(OUT / f'{key}.png'), full_page=False)
                    result[key] = 'shot'
                else:
                    result[key] = {
                        'sidebar': rect(page, 'aside.sidebar'),
                        'header': rect(page, 'main>header'),
                        'main': rect(page, 'main'),
                        'welcome': rect(page, '#welcome'),
                        'welcomeActive': page.evaluate("!!document.querySelector('#welcome.active')"),
                        'nav': nav_items(page),
                        'fonts': fonts(page),
                        'homeButtons': page.evaluate(
                            """()=>[...document.querySelectorAll('#welcome button, #welcome a')]
                            .filter(e=>e.offsetParent).map(e=>e.innerText.replace(/\\s+/g,' ').trim())"""
                        ),
                        'theme': page.evaluate("document.documentElement.dataset.theme"),
                    }
                page.close()
        browser.close()
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
