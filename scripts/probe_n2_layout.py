#!/usr/bin/env python3
"""n17 (задача N2) — замер игрового экрана: лента, композер, шапка.

Поднимает тот же стенд, что `tools/ui_audit.py` (свой профиль, свободный
порт, три кампании через Nat20-движок), и меряет то, чего в 40-кадровом
замере нет: зазор «последнее сообщение → поле ввода», поля композера,
ширину названия кампании в шапке и зазор до клавиатуры.

Запуск:
    .venv/bin/python scripts/probe_n2_layout.py            # 375, 375 с клавиатурой, 1440
    .venv/bin/python scripts/probe_n2_layout.py --tag n17 --shots
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


MEASURE = r"""
(() => {
  const px = v => Math.round(v * 10) / 10;
  const rect = e => { if (!e) return null; const r = e.getBoundingClientRect();
    return {x: px(r.x), y: px(r.y), w: px(r.width), h: px(r.height),
            bottom: px(r.bottom), right: px(r.right)}; };
  const header = document.querySelector('body > main > header');
  const title = header && header.querySelector('.m-title');
  const sub = header && header.querySelector('.m-sub');
  const chat = document.querySelector('#play #chat');
  const composer = document.querySelector('#play .composer');
  const form = document.querySelector('#play #action-form');
  const input = document.querySelector('#play #action-form textarea');
  const send = document.querySelector('#play #send');
  const messages = chat ? [...chat.querySelectorAll('.message, .proposal')] : [];
  const last = messages.length ? messages[messages.length - 1] : null;
  const visible = e => e && e.getBoundingClientRect().height > 0;
  const cs = form ? getComputedStyle(form) : null;
  const ccs = composer ? getComputedStyle(composer) : null;
  const body = document.querySelector('#play');
  const card = chat ? chat.querySelector('.message .prose') : null;
  const bid = body ? body.getBoundingClientRect() : null;
  return {
    viewport: {w: innerWidth, h: innerHeight},
    play: rect(body),
    chat: rect(chat),
    lastMessage: rect(last),
    composer: rect(composer),
    form: rect(form),
    input: rect(input),
    send: rect(send),
    card: rect(card),
    formPadding: cs ? [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft] : null,
    composerPadding: ccs ? [ccs.paddingTop, ccs.paddingRight, ccs.paddingBottom, ccs.paddingLeft] : null,
    /* Зазор «конец текста → рамка поля»: от низа последнего сообщения до
       верха формы. Это и есть «пустота» из замечания №1. */
    textToInput: (last && form) ? px(form.getBoundingClientRect().top - last.getBoundingClientRect().bottom) : null,
    /* От верха ленты до текста, если лента прокручена к началу — не нужен. */
    chatToForm: (chat && form) ? px(form.getBoundingClientRect().top - chat.getBoundingClientRect().bottom) : null,
    /* Сколько осталось до низа кадра (клавиатуры): норма 8. */
    bottomGap: composer ? px(innerHeight - composer.getBoundingClientRect().bottom) : null,
    header: rect(header),
    title: title ? {text: title.textContent.trim(), w: px(title.getBoundingClientRect().width),
                    client: title.clientWidth, scroll: title.scrollWidth,
                    font: getComputedStyle(title).fontSize} : null,
    sub: visible(sub) ? {text: sub.textContent.trim(), w: px(sub.getBoundingClientRect().width),
                         client: sub.clientWidth, scroll: sub.scrollWidth,
                         font: getComputedStyle(sub).fontSize} : null,
    vitals: rect(header && header.querySelector('.vitals')),
    navDebug: (() => {
      const aside = document.querySelector('body > aside.sidebar');
      const b = aside && aside.querySelector('button.nav');
      if (!b) return {aside: Boolean(aside), found: false};
      const cs = getComputedStyle(b);
      const r = b.getBoundingClientRect();
      return {found: true, cls: b.className, w: px(r.width), h: px(r.height),
              minH: cs.minHeight, height: cs.height, pad: cs.padding,
              inAside: aside.contains(b), parent: b.parentElement.className,
              mq900: matchMedia('(min-width:901px)').matches,
              mq701: matchMedia('(min-height:701px)').matches};
    })(),
    vitalsDebug: (() => {
      const v = header && header.querySelector('.vitals');
      if (!v) return null;
      const cs = getComputedStyle(v);
      return {display: cs.display, font: cs.fontSize, gap: cs.gap, flex: cs.flex,
              html: v.outerHTML.slice(0, 300),
              kids: [...v.children].map(k => ({cls: k.className,
                disp: getComputedStyle(k).display,
                w: px(k.getBoundingClientRect().width), h: px(k.getBoundingClientRect().height)}))};
    })(),
    taps: header ? [...header.querySelectorAll('button')].filter(visible).map(b => {
      const r = b.getBoundingClientRect();
      return {id: b.id || b.className, w: px(r.width), h: px(r.height)};
    }) : [],
    boxes: ['body > main', 'body > aside.sidebar', '.workspace-layout', '.story-card',
            '.literary-column', '#chat', '.scene-rail', '#scene-hero'].reduce((acc, sel) => {
      const node = document.querySelector(sel);
      acc[sel] = node ? rect(node) : null;
      if (sel === '#chat' && node) {
        acc['chat > .message'] = rect(node.querySelector('.message'));
        const cs = getComputedStyle(node);
        acc['chatRect'][sel] = {padding: cs.padding, display: cs.display,
                                justify: cs.justifyContent, direction: cs.flexDirection};
      }
      return acc;
    }, {chatRect: {}}),
    headerKids: header ? [...header.children].filter(visible).map(e => {
      const r = e.getBoundingClientRect();
      return {cls: e.className || e.tagName, x: px(r.x), w: px(r.width), h: px(r.height),
              order: getComputedStyle(e).order, text: (e.innerText || '').slice(0, 28)};
    }) : [],
    interactive: [...document.querySelectorAll('a,button,[role=button],input,textarea,select,summary')]
      .filter(visible).map(e => {
        const r = e.getBoundingClientRect();
        return {cls: (e.className || e.tagName).toString().slice(0, 34), id: e.id || '',
                w: px(r.width), h: px(r.height),
                text: (e.innerText || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ').slice(0, 30)};
      }),
    sheet: (() => {
      const b = document.querySelector('#play #scene-hero .sheet-link') || document.querySelector('.sheet-link');
      if (!b) return null;
      const cs = getComputedStyle(b);
      return {minHeight: cs.minHeight, height: cs.height, display: cs.display,
              parent: b.parentElement ? (b.parentElement.className || b.parentElement.tagName) : null,
              inHero: Boolean(b.closest('#scene-hero')), inPlay: Boolean(b.closest('#play'))};
    })()
  };
})()
"""

TITLES = r"""
(() => {
  const names = ['Тайна трёх сосен', 'Соляной тракт', 'Дом у маяка'];
  const label = document.querySelector('#campaign-label');
  const out = [];
  for (const name of names) {
    if (label) label.textContent = name;
    if (window.soloHeader) window.soloHeader.syncTitle();
    const line = document.querySelector('body > main > header .m-title > span');
    const sub = document.querySelector('body > main > header .m-sub');
    out.push({name: name,
              title: line ? line.textContent : null,
              client: line ? line.clientWidth : null,
              scroll: line ? line.scrollWidth : null,
              sub: sub ? sub.textContent : null,
              subScroll: sub ? sub.scrollWidth : null,
              subClient: sub ? sub.clientWidth : null});
  }
  return out;
})()
"""

FRAMES = [(375, 812, False, 'game-375'), (375, 476, True, 'game-kb-375'), (1440, 900, False, 'game-1440')]


def start_fast(audit, data_dir: Path):
    """Сервер стенда без фикстуры подготовки (она нужна только кадрам prep).

    `ui_audit.start_server` ждёт `fixture.json` до 240 с — для игровых кадров
    это лишние минуты: поднимаем тот же процесс и ждём только `/api/health`.
    """
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
                        return proc, port, info['campaigns'], None
                    except Exception:
                        time.sleep(0.25)
        except Exception:
            pass
        time.sleep(0.25)
    proc.kill()
    raise SystemExit('сервер не поднялся за 120 с')


def run(tag: str, shots: bool, only: str) -> dict:
    audit = harness()
    data_dir = ROOT / 'runtime' / f'{tag}-probe'
    data_dir.mkdir(parents=True, exist_ok=True)
    server, port, campaigns, _fixture = start_fast(audit, data_dir)
    base = f'http://127.0.0.1:{port}'
    chrome, cdp_port = audit.start_chrome(data_dir / 'chrome-profile')
    rows = []
    try:
        cdp = audit.CDP(audit.page_target(cdp_port))
        cdp.call('Page.enable')
        cdp.call('Runtime.enable')
        cdp.call('Network.enable')
        cdp.call('Network.setCacheDisabled', cacheDisabled=True)
        cdp.call('Page.addScriptToEvaluateOnNewDocument', source=(
            "(()=>{try{const q=new URLSearchParams(location.search);"
            "const t=q.get('theme');if(t)localStorage.setItem('solo-theme',t);"
            "localStorage.setItem('solo-start-page','welcome');"
            "const c=q.get('campaign');if(c)localStorage.setItem('solo-campaign',c);"
            "}catch(e){}})();"))
        for width, height, kb, name in FRAMES:
            if only and only not in name:
                continue
            got = open_frame(audit, cdp, base, campaigns, width, height, kb, name, shots)
            rows.append(got)
            print(json.dumps(got, ensure_ascii=False), flush=True)
    finally:
        try:
            cdp.close()
        except Exception:
            pass
        chrome.terminate()
        server.terminate()
        for proc in (chrome, server):
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
    return {'base': base, 'frames': rows}


def open_frame(audit, cdp, base, campaigns, width, height, kb, name, shots):
    campaign = campaigns[0]
    cdp.call('Emulation.setDeviceMetricsOverride', width=width, height=height,
             deviceScaleFactor=1, mobile=width < 700)
    out = {'frame': name}
    for _attempt in range(3):
        cdp.call('Page.navigate', url=f'{base}/?theme=dark&campaign={campaign}&probe={name}&t={time.time()}')
        if cdp.wait_js(audit.WAIT_READY, 90):
            break
        time.sleep(1.0)
    cdp.wait_js("!!window.soloMenu && !!document.querySelector('#nav-drawer')", 40)
    time.sleep(0.7)
    cdp.wait_js("document.body.classList.contains('campaign-open')", 20)
    cdp.eval(audit.setup_game(width))
    cdp.wait_js("!!document.querySelector('#play.view.active')", 15)
    cdp.wait_js("document.querySelectorAll('body > main > header .vitals__label').length >= 2", 15)
    time.sleep(1.0)
    cdp.eval("(()=>{const c=document.querySelector('#play #chat');if(c)c.scrollTop=c.scrollHeight;})()")
    if kb:
        cdp.eval("document.body.classList.add('kb-open')")
        time.sleep(0.4)
    out['measured'] = cdp.eval(MEASURE)
    if shots:
        shot = cdp.call('Page.captureScreenshot', format='png')
        target = ROOT / 'reports/design-review' / f'{Path(__file__).stem}-{name}.png'
        target.write_bytes(base64.b64decode(shot['data']))
        out['file'] = str(target.relative_to(ROOT))
    if width < 700:
        out['titles'] = cdp.eval(TITLES)
    errors = [e for e in cdp.events if e.get('method') == 'Runtime.exceptionThrown']
    out['jsErrors'] = [json.dumps(e['params'].get('exceptionDetails', {}).get('text', ''))[:120]
                       for e in errors[-3:]]
    cdp.events.clear()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', default='n17')
    parser.add_argument('--shots', action='store_true')
    parser.add_argument('--only', default='')
    args = parser.parse_args()
    result = run(args.tag, args.shots, args.only)
    target = ROOT / 'reports/design-review' / f'{args.tag}-game-layout.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('файл:', target.relative_to(ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(main())
