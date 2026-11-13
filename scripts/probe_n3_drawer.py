#!/usr/bin/env python3
"""N3 — шторка меню на телефоне: лист снизу, тап мимо, вкладки парой, выход.

Поднимает тот же стенд, что `tools/ui_audit.py` (свой профиль, свободный порт,
кампании сеет Nat20-движок), и меряет по живой странице то, чего нет в
40-кадровом замере: где стоит лист и куда он доходит, что лежит под пальцем
выше листа, чем отличаются вкладки, где «Выход» и требует ли он
подтверждения. Снимки — `reports/N3/*.png`.

Запуск:
    .venv/bin/python scripts/probe_n3_drawer.py
    .venv/bin/python scripts/probe_n3_drawer.py --tag n18
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def harness():
    spec = importlib.util.spec_from_file_location('ui_audit', ROOT / 'tools/ui_audit.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['ui_audit'] = module
    spec.loader.exec_module(module)
    return module


# ── замер шторки ────────────────────────────────────────────────────────────
MEASURE = r"""
(() => {
  const px = v => Math.round(v * 10) / 10;
  const rect = e => { if (!e) return null; const r = e.getBoundingClientRect();
    return {x: px(r.x), y: px(r.y), w: px(r.width), h: px(r.height),
            bottom: px(r.bottom), right: px(r.right)}; };
  const seen = e => e && e.getBoundingClientRect().height > 0;
  const aside = document.querySelector('body > aside.sidebar');
  const drawer = document.querySelector('#nav-drawer');
  const head = document.querySelector('#nav-head');
  const tabs = document.querySelector('#nav-tabs');
  const tabNodes = [...document.querySelectorAll('button.nav-tab')];
  const exit = document.querySelector('aside.sidebar .nav-exit');
  const scrim = document.querySelector('.m-scrim');
  const cs = aside ? getComputedStyle(aside) : null;
  const scs = scrim ? getComputedStyle(scrim) : null;
  /* Что лежит под пальцем выше листа: затемнение (закрывает) или живая кнопка
     полосы. Прежняя панель от 0 перекрывала весь экран, и здесь была кнопка. */
  const above = document.elementFromPoint(Math.round(innerWidth / 2), 60);
  const rows = [...document.querySelectorAll('#nav-drawer button.nav, #nav-drawer a.nav')].filter(seen);
  return {
    viewport: {w: innerWidth, h: innerHeight},
    open: document.body.classList.contains('mobile-menu-open'),
    sheet: rect(aside),
    sheetStyle: cs ? {background: cs.backgroundColor, borderTop: cs.borderTopWidth + ' ' + cs.borderTopColor,
                      radius: cs.borderTopLeftRadius, minHeight: cs.minHeight, maxHeight: cs.maxHeight,
                      paddingBottom: cs.paddingBottom, overflow: cs.overflowY} : null,
    /* Зазор «верх кадра → верх листа»: норма 124…154 px (SPEC §1). */
    topGap: aside ? px(aside.getBoundingClientRect().top) : null,
    /* Зазор «низ экрана → низ листа»: норма 0, лист доходит до низа. */
    bottomGap: aside ? px(innerHeight - aside.getBoundingClientRect().bottom) : null,
    scrim: scrim ? {rect: rect(scrim), opacity: scs.opacity, opacityNum: Number(scs.opacity),
                    zIndex: scs.zIndex, pointer: scs.pointerEvents,
                    background: scs.backgroundColor} : null,
    pageBg: getComputedStyle(document.body).backgroundColor,
    above: above ? {cls: (above.className || above.tagName).toString().slice(0, 40),
                    text: (above.innerText || '').replace(/\s+/g, ' ').slice(0, 32),
                    view: (above.closest('[data-view]') || {}).dataset?.view || null} : null,
    head: rect(head),
    tabs: tabs ? {rect: rect(tabs), borderBottom: getComputedStyle(tabs).borderBottomWidth + ' ' + getComputedStyle(tabs).borderBottomColor} : null,
    tabItems: tabNodes.map(b => {
      const s = getComputedStyle(b);
      return {text: (b.textContent || '').trim(),
              selected: b.getAttribute('aria-selected') === 'true',
              rect: rect(b), color: s.color, background: s.backgroundColor,
              border: s.borderTopWidth + ' ' + s.borderTopColor,
              borderBottom: s.borderBottomWidth + ' ' + s.borderBottomColor};
    }),
    groupLabels: [...document.querySelectorAll('#nav-drawer .nav-group-label')].filter(seen).length,
    drawer: drawer ? {rect: rect(drawer), scroll: drawer.scrollHeight, client: drawer.clientHeight,
                      overflow: Math.max(0, drawer.scrollHeight - drawer.clientHeight),
                      scrollTop: Math.round(drawer.scrollTop)} : null,
    rows: rows.map(b => {const r = b.getBoundingClientRect(); const s = getComputedStyle(b);
      return {text: (b.innerText || '').replace(/\s+/g, ' ').trim(), y: px(r.y), h: px(r.height),
              bottom: px(r.bottom), font: s.fontSize};}),
    exit: exit ? {rect: rect(exit), text: (exit.innerText || '').trim(),
                  style: (() => {const s = getComputedStyle(exit);
                    return {position: s.position, bottom: s.bottom, padding: s.padding,
                            minHeight: s.minHeight, background: s.backgroundColor,
                            borderTop: s.borderTopWidth + ' ' + s.borderTopColor};})()} : null,
    /* Зазор «последняя строка → Выход»: было 5 px, норма — не меньше 12. */
    rowToExit: (() => {
      if (!exit || !rows.length) return null;
      const before = rows.filter(b => b !== exit);
      if (!before.length) return null;
      const last = Math.max(...before.map(b => b.getBoundingClientRect().bottom));
      return px(exit.getBoundingClientRect().top - last);
    })(),
    /* Зазор «низ Выхода → низ экрана»: было 20 px, теперь 12 + safe-area. */
    exitToBottom: exit ? px(innerHeight - exit.getBoundingClientRect().bottom) : null,
    fonts: {exit: exit ? getComputedStyle(exit).fontSize : null},
    debug: (() => {
      const first = rows.length ? rows[0] : null;
      const es = exit ? getComputedStyle(exit) : null;
      const rowStyle = first ? getComputedStyle(first) : null;
      const block = document.querySelector('#nav-drawer .nav-block');
      const bcs = block ? getComputedStyle(block) : null;
      return {
        exitMargin: es ? [es.marginTop, es.marginBottom] : null,
        exitParent: exit && exit.parentElement ? exit.parentElement.className : null,
        tabletop: Boolean(exit && exit.closest('.tabletop')),
        rowMargin: rowStyle ? [rowStyle.marginTop, rowStyle.marginBottom] : null,
        rowDisplay: rowStyle ? rowStyle.display : null,
        blockGap: bcs ? bcs.gap : null,
        blockDisplay: bcs ? bcs.display : null,
        drawerDisplay: drawer ? getComputedStyle(drawer).display : null,
        drawerGap: drawer ? getComputedStyle(drawer).gap : null,
        drawerKids: drawer ? [...drawer.children].map(c => c.className.split(' ')[0]) : null,
      };
    })(),
    errors: (window.__soloProbeErrors || []).slice(0, 3)
  };
})()
"""

# ── подтверждение выхода ────────────────────────────────────────────────────
EXIT_STATE = r"""
(() => {
  const dialog = document.querySelector('#exit-confirm');
  const play = document.querySelector('#play');
  const welcome = document.querySelector('#welcome');
  return {
    hasDialog: Boolean(dialog),
    dialogOpen: Boolean(dialog && dialog.open),
    dialogMarkup: dialog ? (dialog.innerText || '').replace(/\s+/g, ' ').trim() : null,
    modal: dialog ? dialog.matches(':modal') : null,
    exitRow: [...document.querySelectorAll('#nav-drawer [data-view="exit"]')].length,
    campaignOpen: document.body.classList.contains('campaign-open'),
    playActive: Boolean(play && play.classList.contains('active')),
    welcomeActive: Boolean(welcome && welcome.classList.contains('active')),
    campaignIdStored: localStorage.getItem('solo-campaign')
  };
})()
"""

DIALOG_RECT = r"""
(() => {
  const d = document.querySelector('#exit-confirm');
  if (!d) return null;
  const r = d.getBoundingClientRect();
  return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
          bottom: Math.round(r.bottom), yes: Boolean(document.querySelector('#exit-confirm-yes')),
          no: Boolean(document.querySelector('#exit-confirm-no'))};
})()
"""


def contrast(a: str, b: str) -> float:
    """Отношение контраста двух `rgb(...)`-цветов по WCAG 2.1."""
    def lum(color: str) -> float:
        nums = [int(float(x)) for x in color.replace('rgba(', '').replace('rgb(', '')
                .replace(')', '').split(',')[:3]]
        out = []
        for value in nums:
            c = value / 255
            out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


def start_fast(audit, data_dir: Path):
    """Сервер стенда без фикстуры подготовки (она нужна только кадрам prep)."""
    import subprocess
    import urllib.request
    port = audit.free_port()
    log = open(data_dir / 'server.log', 'wb')
    proc = subprocess.Popen([sys.executable, str(ROOT / 'tools/ui_audit.py'), '--serve',
                             '--data-dir', str(data_dir), '--port', str(port)],
                            stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))
    info_path = data_dir / 'server.json'
    deadline = time.time() + 120
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit('сервер упал: ' + (data_dir / 'server.log').read_text(errors='replace')[-2000:])
        try:
            info = json.loads(info_path.read_text(encoding='utf-8'))
            if info.get('port') == port:
                for _ in range(240):
                    try:
                        urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2).read()
                        return proc, port, info['campaigns']
                    except Exception:
                        time.sleep(0.25)
        except Exception:
            pass
        time.sleep(0.25)
    proc.kill()
    raise SystemExit('сервер не поднялся за 120 с')


def shot(cdp, name: str, out_dir: Path) -> str:
    data = cdp.call('Page.captureScreenshot', format='png')
    target = out_dir / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base64.b64decode(data['data']))
    return str(target.relative_to(ROOT))


def click(cdp, x: float, y: float) -> None:
    """Настоящий тап: mousePressed + mouseReleased в точке кадра."""
    for kind in ('mousePressed', 'mouseReleased'):
        cdp.call('Input.dispatchMouseEvent', type=kind, x=x, y=y, button='left',
                 clickCount=1, buttons=1 if kind == 'mousePressed' else 0)


def run(tag: str, theme: str = 'dark') -> dict:
    audit = harness()
    data_dir = ROOT / 'runtime' / f'{tag}-probe'
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir = ROOT / 'reports' / 'N3'
    server, port, campaigns = start_fast(audit, data_dir)
    base = f'http://127.0.0.1:{port}'
    chrome, cdp_port = audit.start_chrome(data_dir / 'chrome-profile')
    result: dict = {'base': base, 'frames': [], 'checks': {}}
    try:
        cdp = audit.CDP(audit.page_target(cdp_port))
        cdp.call('Page.enable')
        cdp.call('Runtime.enable')
        cdp.call('Network.enable')
        cdp.call('Network.setCacheDisabled', cacheDisabled=True)
        cdp.call('Page.addScriptToEvaluateOnNewDocument', source=(
            "(()=>{try{window.__soloProbeErrors=[];"
            "window.addEventListener('error',e=>window.__soloProbeErrors.push(String(e.message)));"
            "const q=new URLSearchParams(location.search);"
            "if(q.get('theme'))localStorage.setItem('solo-theme',q.get('theme'));"
            "localStorage.setItem('solo-start-page','welcome');"
            "if(q.get('campaign'))localStorage.setItem('solo-campaign',q.get('campaign'));"
            "}catch(e){}})();"))
        width, height = 375, 812
        cdp.call('Emulation.setDeviceMetricsOverride', width=width, height=height,
                 deviceScaleFactor=1, mobile=True)
        campaign = campaigns[0]
        for _attempt in range(3):
            cdp.call('Page.navigate', url=f'{base}/?theme={theme}&campaign={campaign}&probe={tag}&t={time.time()}')
            if cdp.wait_js(audit.WAIT_READY, 90):
                break
            time.sleep(1.0)
        cdp.wait_js("!!window.soloMenu && !!document.querySelector('#nav-drawer')", 40)
        cdp.wait_js("document.body.classList.contains('campaign-open')", 20)
        cdp.eval(audit.setup_game(width))
        cdp.wait_js("!!document.querySelector('#play.view.active')", 15)
        time.sleep(1.0)

        # Зазор подписи «сохранено» до системной полосы (добавка оценщика).
        result['checks']['turn'] = cdp.eval("""(async()=>{
          try{
            const id=localStorage.getItem('solo-campaign');
            const c=await (await fetch('/api/campaigns/'+id)).json();
            const r=await fetch('/api/campaigns/'+id+'/command-turn',{method:'POST',
              headers:{'Content-Type':'application/json'},
              body:JSON.stringify({request_id:crypto.randomUUID(),text:'осматриваюсь по сторонам',
                                   expected_version:c.version})});
            const body=await r.json().catch(()=>null);
            return {status:r.status, turn:body&&body.state?body.state.turn:null};
          }catch(e){return {error:String(e)};}
        })()""")
        cdp.call('Page.navigate', url=f'{base}/?theme={theme}&campaign={campaign}&probe={tag}-turn&t={time.time()}')
        cdp.wait_js(audit.WAIT_READY, 90)
        cdp.wait_js("document.body.classList.contains('campaign-open')", 20)
        cdp.eval(audit.setup_game(width))
        cdp.wait_js("!!document.querySelector('#play.view.active')", 15)
        time.sleep(1.0)
        result['checks']['gameNote'] = cdp.eval("""(()=>{
          const row=document.querySelector('#play .dice-row'), note=document.querySelector('#play .dice-row__note');
          if(!row||!note) return null;
          const r=note.getBoundingClientRect(), rr=row.getBoundingClientRect();
          return {text:(note.textContent||'').trim(), noteTop:Math.round(r.top),
                  noteBottom:Math.round(r.bottom), rowBottom:Math.round(rr.bottom),
                  gapToScreen:Math.round(innerHeight-rr.bottom),
                  noteGapToScreen:Math.round(innerHeight-r.bottom)};
        })()""")
        result['checks']['gameNoteShot'] = shot(cdp, f'game-dock-{theme}-375.png', out_dir)

        for tab, name in (('campaign', f'menu-campaign-{theme}-375.png'), ('app', f'menu-app-{theme}-375.png')):
            cdp.eval(audit.setup_menu(tab == 'app'))
            cdp.wait_js("document.body.classList.contains('mobile-menu-open')", 10)
            time.sleep(0.5)
            measured = cdp.eval(MEASURE)
            measured['tab'] = tab
            if measured.get('sheet') and measured.get('sheetStyle'):
                # Цвета берём только из DOM: в светлой теме `--candle` и стол
                # другие, и подставленный тёмный цвет дал бы ложное число.
                sheet_bg = measured['sheetStyle']['background']
                page_bg = measured['pageBg']
                selected = next(t for t in measured['tabItems'] if t['selected'])
                unselected = next(t for t in measured['tabItems'] if not t['selected'])
                measured['contrast'] = {
                    'sheet/page': contrast(sheet_bg, page_bg),
                    'sheetEdge/sheet': contrast(
                        measured['sheetStyle']['borderTop'].split(' ', 1)[1], sheet_bg),
                    'tabUnselectedText/sheet': contrast(unselected['color'], sheet_bg),
                    'tabSelectedUnderline/sheet': contrast(
                        selected['borderBottom'].split(' ', 1)[1], sheet_bg),
                    'tabSelectedText/sheet': contrast(selected['color'], sheet_bg),
                }
            measured['file'] = shot(cdp, name, out_dir)
            result['frames'].append(measured)
            # Закрыть шторку крестиком перед следующей вкладкой.
            cdp.eval("(()=>{const b=document.querySelector('.nav-head .nav-close');if(b)b.click();})()")
            time.sleep(0.4)

        # ── тап мимо шторки: по затемнению выше листа ──────────────────────
        cdp.eval(audit.setup_menu(False))
        cdp.wait_js("document.body.classList.contains('mobile-menu-open')", 10)
        time.sleep(0.5)
        before = cdp.eval(EXIT_STATE)
        under = cdp.eval("""(()=>{const e=document.elementFromPoint(187,60);
          return e?{cls:(e.className||e.tagName).toString().slice(0,40),
                    view:(e.closest('[data-view]')||{}).dataset?.view||null}:null;})()""")
        click(cdp, 187, 60)
        time.sleep(0.5)
        after_click = cdp.eval("""(()=>({open:document.body.classList.contains('mobile-menu-open'),
          activeView:(document.querySelector('body > main > .view.active')||{}).id||null,
          exitDialog:Boolean(document.querySelector('#exit-confirm')&&document.querySelector('#exit-confirm').open)}))()""")
        result['checks']['tapOutside'] = {'under': under, 'before': before, 'after': after_click}

        # ── выход: подтверждение и отмена ─────────────────────────────────
        cdp.eval(audit.setup_menu(False))
        cdp.wait_js("document.body.classList.contains('mobile-menu-open')", 10)
        time.sleep(0.4)
        cdp.eval("""(()=>{const b=document.querySelector('#nav-drawer [data-view="exit"]');
          if(b)b.click();return Boolean(b);})()""")
        time.sleep(0.5)
        asked = cdp.eval(EXIT_STATE)
        asked['rect'] = cdp.eval(DIALOG_RECT)
        asked['file'] = shot(cdp, f'exit-confirm-{theme}-375.png', out_dir)
        result['checks']['exitAsked'] = asked

        cdp.eval("(()=>{const b=document.querySelector('#exit-confirm-no');if(b)b.click();})()")
        time.sleep(0.5)
        result['checks']['exitCancelled'] = cdp.eval(EXIT_STATE)

        # повторно: согласие действительно выводит из кампании
        cdp.eval("""(()=>{if(!document.body.classList.contains('mobile-menu-open')){
          const g=document.querySelector('#mobile-burger');if(g)g.click();}
          const b=document.querySelector('#nav-drawer [data-view="exit"]');if(b)b.click();})()""")
        time.sleep(0.5)
        cdp.eval("(()=>{const b=document.querySelector('#exit-confirm-yes');if(b)b.click();})()")
        time.sleep(0.8)
        result['checks']['exitConfirmed'] = cdp.eval(EXIT_STATE)

        errors = [json.dumps(e['params'].get('exceptionDetails', {}).get('text', ''))[:160]
                  for e in cdp.events if e.get('method') == 'Runtime.exceptionThrown']
        result['jsErrors'] = errors[-3:]
        cdp.close()
    finally:
        chrome.terminate()
        server.terminate()
        for proc in (chrome, server):
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
    target = out_dir / f'{tag}-drawer.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print('файл:', target.relative_to(ROOT))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', default='n18')
    parser.add_argument('--theme', default='dark')
    args = parser.parse_args()
    run(args.tag, args.theme)
    return 0


if __name__ == '__main__':
    sys.exit(main())
