#!/usr/bin/env python3
"""n16 critic — независимый замер экранов game/hero вживую (свой data-dir).

Поднимает инстанс приложения через tools/ui_audit.py, открывает #play и
#character на 375 и 1440, снимает геометрию низа экрана: композер, поле
ввода, кнопка, зазоры, осиротевшие подписи.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import ui_audit as A  # noqa: E402

DATA = ROOT / 'runtime/n16-critic2'


def boot():
    A.DEFAULT_DATA = DATA
    DATA.mkdir(parents=True, exist_ok=True)
    proc, port, campaigns, meta = A.start_server(DATA)
    chrome, cport = A.start_chrome(DATA / 'chrome')
    target = A.page_target(cport)
    cdp = A.CDP(target)
    return proc, port, campaigns, chrome, cport, cdp


PROBE_PLAY = r"""
(() => {
  const vw = innerWidth, vh = innerHeight;
  const r = el => { if(!el) return null; const b = el.getBoundingClientRect();
    return {x:Math.round(b.x), y:Math.round(b.y), w:Math.round(b.width),
            h:Math.round(b.height), bottom:Math.round(b.bottom), right:Math.round(b.right)}; };
  const cs = el => el ? getComputedStyle(el) : null;
  const chat = document.querySelector('#play #chat');
  const blocks = [...document.querySelectorAll('#play #chat > *')].map(el => ({
    cls: el.className || el.tagName, ...r(el), text: (el.innerText||'').replace(/\s+/g,' ').slice(0,80)
  }));
  const lastBlock = blocks.length ? blocks[blocks.length-1] : null;
  const form = document.querySelector('form#action-form');
  const ta = document.querySelector('form#action-form textarea, form#action-form input[type=text]');
  const btn = [...document.querySelectorAll('form#action-form button')].map(b => ({
    text:(b.innerText||'').trim(), type:b.type, ...r(b), fs:cs(b).fontSize,
    bg:cs(b).backgroundColor, color:cs(b).color, disabled:b.disabled,
    label:b.getAttribute('aria-label'), title:b.getAttribute('title')
  }));
  const foot = document.querySelector('form#action-form .composer-footer');
  const emptyish = [...document.querySelectorAll('#play *')].filter(el => {
    if (el.children.length) return false;
    const t = (el.innerText||'').trim(); if (!t) return false;
    const b = el.getBoundingClientRect(); if (!b.width || b.y < 0 || b.bottom > vh) return false;
    return true;
  }).map(el => { const b=el.getBoundingClientRect(); return {cls:el.className,
      text:el.innerText.trim().slice(0,50), x:Math.round(b.x), y:Math.round(b.y),
      w:Math.round(b.width), fs:cs(el).fontSize, color:cs(el).color,
      clickable: !!el.closest('button,a,summary,label,[onclick],[role=button]')}; });
  const inter = [...document.querySelectorAll('#play button, #play a, #play summary, #play [role=button], #play input, #play textarea, #play label, #play select')]
    .filter(el => { const b = el.getBoundingClientRect(); const s = getComputedStyle(el);
      return b.width>1 && b.height>1 && b.bottom>0 && b.y<vh && s.visibility!=='hidden' && s.display!=='none'; })
    .map(el => { const b=el.getBoundingClientRect(); return {tag:el.tagName.toLowerCase(), cls:el.className,
      text:(el.innerText||el.value||el.getAttribute('aria-label')||'').trim().slice(0,40),
      w:Math.round(b.width), h:Math.round(b.height), x:Math.round(b.x), y:Math.round(b.y)}; });
  const header = document.querySelector('#play header') || document.querySelector('main > header');
  const main = document.querySelector('#play');
  return {vw, vh, activeView:(document.querySelector('.view.active')||{}).id,
    mainRect:r(main), headerRect:r(header),
    headerText:(header?header.innerText:'').replace(/\s+/g,' ').slice(0,200),
    chatRect:r(chat), chatScrollH: chat?chat.scrollHeight:0, chatClientH: chat?chat.clientHeight:0,
    blocks, blockCount: blocks.length, lastBlock,
    gapLastBlockToComposer: (lastBlock && form) ? Math.round(form.getBoundingClientRect().top - lastBlock.bottom) : null,
    gapComposerBottomToViewport: form ? Math.round(vh - form.getBoundingClientRect().bottom) : null,
    form:r(form), formBg: cs(form)?cs(form).backgroundColor:null, formBorder: cs(form)?cs(form).borderTopColor:null,
    ta: ta?{...r(ta), fs:cs(ta).fontSize, color:cs(ta).color, ph:ta.getAttribute('placeholder'), h:Math.round(ta.getBoundingClientRect().height)}:null,
    btns: btn, foot: foot?{...r(foot), text:foot.innerText.trim().slice(0,80), fs:cs(foot).fontSize}:null,
    interactive: inter, interactiveCount: inter.length,
    tapTargetsUnder44: inter.filter(e => e.h<44 || e.w<44).length,
    texts: emptyish, textCount: emptyish.length
  };
})()
"""

PROBE_HERO = r"""
(() => {
  const vh = innerHeight;
  const r = el => { if(!el) return null; const b=el.getBoundingClientRect();
    return {x:Math.round(b.x), y:Math.round(b.y), w:Math.round(b.width), h:Math.round(b.height), bottom:Math.round(b.bottom)}; };
  const cs = el => el?getComputedStyle(el):null;
  const content = document.querySelector('#character-content');
  const kids = [...content.children].map(el => ({cls:el.className, ...r(el), text:(el.innerText||'').replace(/\s+/g,' ').slice(0,60)}));
  const inter = [...document.querySelectorAll('#character button, #character a, #character summary, #character input, #character textarea')]
    .filter(el=>{const b=el.getBoundingClientRect(); const s=getComputedStyle(el); return b.width>1&&b.height>1&&b.bottom>0&&b.y<vh&&s.display!=='none';})
    .map(el=>{const b=el.getBoundingClientRect(); return {tag:el.tagName.toLowerCase(), cls:el.className,
      text:(el.innerText||el.value||el.getAttribute('aria-label')||'').trim().slice(0,40),
      w:Math.round(b.width), h:Math.round(b.height), x:Math.round(b.x), y:Math.round(b.y), fs:cs(el).fontSize};});
  const texts = [...document.querySelectorAll('#character *')].filter(el=>!el.children.length && (el.innerText||'').trim())
    .map(el=>{const b=el.getBoundingClientRect(); return {sel:el.tagName.toLowerCase()+'.'+String(el.className).split(' ')[0],
      text:el.innerText.trim().slice(0,30), y:Math.round(b.y), x:Math.round(b.x), fs:cs(el).fontSize, color:cs(el).color,
      clickable: !!el.closest('button,a,summary')};});
  const gaps = []; for (let i=1;i<kids.length;i++) gaps.push({after:kids[i-1].cls, gap:kids[i].y - kids[i-1].bottom});
  return {vh, activeView:(document.querySelector('.view.active')||{}).id,
    content:r(content), contentScrollH:content?content.scrollHeight:0, contentClientH:content?content.clientHeight:0,
    kids, gaps, inter, interCount: inter.length, texts, textCount: texts.length,
    idles: texts.filter(t=>!t.clickable && /лист|герой|навык|инвентар|уров/i.test(t.text))};
})()
"""


def main():
    proc, port, campaigns, chrome, cport, cdp = boot()
    out = {'port': port, 'campaigns': campaigns, 'frames': {}}
    base = f'http://127.0.0.1:{port}'
    campaign = campaigns[0]
    try:
        cdp.call('Page.enable')
        for w, h in ((375, 812), (1440, 900)):
            cdp.call('Emulation.setDeviceMetricsOverride', width=w, height=h, deviceScaleFactor=1, mobile=w < 700)
            url = f'{base}/?theme=dark&campaign={campaign}&audit=critic-{w}'
            for attempt in range(2):
                cdp.call('Page.navigate', url=url)
                if cdp.wait_js(A.WAIT_READY, 90):
                    break
                time.sleep(1.0)
            cdp.wait_js("document.body.classList.contains('campaign-open')", 20)
            time.sleep(0.8)
            cdp.eval(A.setup_game(w))
            time.sleep(1.0)
            out['frames'][f'play-{w}'] = cdp.eval(PROBE_PLAY)
            cdp.eval(A.setup_hero(w))
            time.sleep(0.8)
            out['frames'][f'hero-{w}'] = cdp.eval(PROBE_HERO)
    finally:
        try:
            cdp.close()
        except Exception:
            pass
        chrome.kill()
        proc.kill()
    (ROOT / 'reports/design-review/n16-critic-probe.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print('ok', {k: (v.get('interactiveCount', v.get('interCount'))) for k, v in out['frames'].items()})


if __name__ == '__main__':
    main()
