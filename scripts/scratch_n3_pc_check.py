#!/usr/bin/env python3
"""Разовый осмотр меню на 1440: что в полосе и что в шторке."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location('ui_audit', ROOT / 'tools/ui_audit.py')
audit = importlib.util.module_from_spec(spec)
sys.modules['ui_audit'] = audit
spec.loader.exec_module(audit)

sys.path.insert(0, str(ROOT / 'scripts'))
from probe_n3_drawer import start_fast  # noqa: E402

data_dir = ROOT / 'runtime' / 'n18-debug-pc'
data_dir.mkdir(parents=True, exist_ok=True)
server, port, campaigns = start_fast(audit, data_dir)
chrome, cdp_port = audit.start_chrome(data_dir / 'chrome-profile')
try:
    cdp = audit.CDP(audit.page_target(cdp_port))
    cdp.call('Page.enable')
    cdp.call('Runtime.enable')
    cdp.call('Emulation.setDeviceMetricsOverride', width=1440, height=900,
             deviceScaleFactor=1, mobile=False)
    cdp.call('Page.addScriptToEvaluateOnNewDocument', source=(
        "(()=>{try{localStorage.setItem('solo-theme','dark');"
        f"localStorage.setItem('solo-campaign','{campaigns[0]}');"
        "localStorage.setItem('solo-start-page','welcome');}catch(e){}})();"))
    cdp.call('Page.navigate', url=f'http://127.0.0.1:{port}/?t={time.time()}')
    cdp.wait_js(audit.WAIT_READY, 90)
    cdp.wait_js("!!window.soloMenu", 40)
    time.sleep(1.0)
    print(json.dumps(cdp.eval(r"""(() => {
      const aside=document.querySelector('body > aside.sidebar');
      const nav=aside.querySelector('nav');
      const bar=nav.querySelector('.nav-bar');
      const toggle=document.querySelector('#nav-more');
      const sig=window.soloMenu.signature();
      return {
        mq: matchMedia('(max-width: 760px)').matches,
        navKids: [...nav.children].map(c=>c.className),
        barKids: bar ? [...bar.children].map(c=>(c.dataset.view||c.id||c.tagName)+':'+(c.innerText||'').trim()) : null,
        barRect: bar ? (()=>{const r=bar.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,display:getComputedStyle(bar).display};})() : null,
        toggle: toggle ? {w:toggle.getBoundingClientRect().width, display:getComputedStyle(toggle).display} : null,
        asideClass: aside.className,
        sig: sig,
        drawerRect: (()=>{const d=document.querySelector('#nav-drawer');const r=d.getBoundingClientRect();
          return {x:r.x,y:r.y,w:r.width,h:r.height,display:getComputedStyle(d).display};})()
      };
    })()"""), ensure_ascii=False, indent=1))
    cdp.eval("(()=>{const b=document.querySelector('#nav-more');if(b)b.click();})()")
    time.sleep(0.8)
    print('after click:', json.dumps(cdp.eval(r"""(() => {
      const aside=document.querySelector('body > aside.sidebar');
      const d=document.querySelector('#nav-drawer');
      const r=d.getBoundingClientRect();
      return {asideClass:aside.className, drawer:{y:r.y,w:r.width,h:r.height,display:getComputedStyle(d).display},
              bodyClass:document.body.className,
              visible:[...document.querySelectorAll('aside.sidebar a,aside.sidebar button')].filter(e=>e.getBoundingClientRect().height>0).length};
    })()"""), ensure_ascii=False, indent=1))
    cdp.close()
finally:
    chrome.terminate(); server.terminate()
    for p in (chrome, server):
        try: p.wait(timeout=10)
        except Exception: p.kill()
