#!/usr/bin/env python3
"""Разовый разбор высоты листа: как Chrome считает min/max-height в процентах."""
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

data_dir = ROOT / 'runtime' / 'n18-debug'
data_dir.mkdir(parents=True, exist_ok=True)
server, port, campaigns = start_fast(audit, data_dir)
chrome, cdp_port = audit.start_chrome(data_dir / 'chrome-profile')
try:
    cdp = audit.CDP(audit.page_target(cdp_port))
    cdp.call('Page.enable')
    cdp.call('Runtime.enable')
    cdp.call('Emulation.setDeviceMetricsOverride', width=375, height=812,
             deviceScaleFactor=1, mobile=True)
    cdp.call('Page.navigate', url=f'http://127.0.0.1:{port}/?theme=dark&campaign={campaigns[0]}&t={time.time()}')
    cdp.wait_js(audit.WAIT_READY, 90)
    cdp.wait_js("!!window.soloMenu", 40)
    cdp.eval(audit.setup_menu(True))
    time.sleep(0.6)
    print(json.dumps(cdp.eval(r"""(() => {
      const a=document.querySelector('body > aside.sidebar');
      const out={};
      const read=(tag)=>{const s=getComputedStyle(a),r=a.getBoundingClientRect();
        out[tag]={h:Math.round(r.height),top:Math.round(r.top),min:s.minHeight,max:s.maxHeight,
                  style:a.getAttribute('style')||''};
        return out[tag];};
      read('as-is');
      a.style.setProperty('min-height','0','important'); read('min0');
      a.style.setProperty('max-height','none','important'); read('max-none');
      a.style.removeProperty('min-height'); read('min-back');
      a.style.setProperty('max-height','calc(100% - 100px)','important'); read('max-100');
      a.style.setProperty('max-height','688px','important'); read('max-688');
      a.style.setProperty('max-height','calc(100dvh - 124px)','important'); read('max-dvh');
      const b=document.body;
      out.body={h:Math.round(b.getBoundingClientRect().height), vvh:getComputedStyle(b).getPropertyValue('--vvh')};
      out.viewport={w:innerWidth,h:innerHeight,vv:window.visualViewport?Math.round(window.visualViewport.height):null};
      return out;})()"""), ensure_ascii=False, indent=1))
    cdp.close()
finally:
    chrome.terminate(); server.terminate()
    for p in (chrome, server):
        try: p.wait(timeout=10)
        except Exception: p.kill()
