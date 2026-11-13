#!/usr/bin/env python3
"""Диагностика: какие именно подзапросы PROBE_PLAY вешают CDP."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import ui_audit as A  # noqa: E402

DATA = ROOT / 'runtime/n16-critic2'
A.DEFAULT_DATA = DATA
DATA.mkdir(parents=True, exist_ok=True)
proc, port, campaigns, meta = A.start_server(DATA)
chrome, cport = A.start_chrome(DATA / 'chrome')
cdp = A.CDP(A.page_target(cport))
try:
    cdp.call('Page.enable')
    cdp.call('Emulation.setDeviceMetricsOverride', width=375, height=812, deviceScaleFactor=1, mobile=True)
    cdp.call('Page.navigate', url=f'http://127.0.0.1:{port}/?theme=dark&campaign={campaigns[0]}&audit=critic-375')
    print('ready', cdp.wait_js(A.WAIT_READY, 90), flush=True)
    cdp.wait_js("document.body.classList.contains('campaign-open')", 20)
    time.sleep(0.8)
    cdp.eval(A.setup_game(375))
    time.sleep(1.0)
    steps = {
        'count': "document.querySelectorAll('#play *').length",
        'form': "(()=>{const f=document.querySelector('form#action-form');return f?f.tagName:'none'})()",
        'blocks': "document.querySelectorAll('#play #chat > *').length",
        'btns': "[...document.querySelectorAll('form#action-form button')].map(b=>b.innerText)",
        'interactive': "document.querySelectorAll('#play button, #play a, #play summary').length",
        'leaftext': "[...document.querySelectorAll('#play *')].filter(e=>!e.children.length&&(e.innerText||'').trim()).length",
        'geometry': "(()=>{const f=document.querySelector('form#action-form').getBoundingClientRect();return [Math.round(f.top),Math.round(f.height)]})()",
    }
    for name, expr in steps.items():
        t = time.time()
        try:
            val = cdp.eval(expr)
            print(name, 'ok', round(time.time() - t, 2), json.dumps(val, ensure_ascii=False)[:200], flush=True)
        except Exception as err:  # noqa: BLE001
            print(name, 'FAIL', round(time.time() - t, 2), type(err).__name__, str(err)[:120], flush=True)
            break
finally:
    try:
        cdp.close()
    except Exception:
        pass
    chrome.kill()
    proc.kill()
