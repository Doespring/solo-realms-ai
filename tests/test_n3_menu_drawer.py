"""N3 — шторка меню на телефоне: закрытие тапом мимо, лист до низа, вкладки,
подтверждение выхода.

Тест гоняет живое приложение в Chromium через тот же CDP-клиент, что
`tools/ui_audit.py` (своя пустая база во временном каталоге, mock-модель, без
сети). Проверяются пять замечаний кадра n16 — числами, не «на глаз».
"""
from __future__ import annotations

import json
import os

os.environ.setdefault('SOLO_TEST', '1')

import socket
import sys
import threading
import time
import importlib.util
from pathlib import Path

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
CHROME = Path.home() / '.cache/ms-playwright/chromium-1248/chrome-linux64/chrome'


def _audit():
    spec = importlib.util.spec_from_file_location('ui_audit', ROOT / 'tools' / 'ui_audit.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['ui_audit'] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def stand(tmp_path):
    """Приложение с одной кампанией и живой Chromium 375×812."""
    if not CHROME.is_file():
        pytest.skip('нет браузера Chromium 1248 — см. tools/ui_audit.py')
    audit = _audit()
    from solo.app import create_app
    from solo.llm import CommandMockLLM
    from solo.nat20_api import Nat20GameEngine

    work = tmp_path / 'n3'
    work.mkdir(parents=True, exist_ok=True)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_app(work / 'n3.sqlite3', CommandMockLLM({}), local_port=port, experimental=True)
    engine = Nat20GameEngine(app.state.store)
    campaign = engine.create_campaign('Мирон', seed=17)['id']
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    chrome, cdp_port = audit.start_chrome(work / 'chrome-profile')
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        cdp = audit.CDP(audit.page_target(cdp_port))
        cdp.call('Page.enable')
        cdp.call('Runtime.enable')
        cdp.call('Network.enable')
        cdp.call('Network.setCacheDisabled', cacheDisabled=True)
        cdp.call('Emulation.setDeviceMetricsOverride', width=375, height=812,
                 deviceScaleFactor=1, mobile=True)
        # Тема и открытая кампания — как у игрока: ключи localStorage до загрузки
        # страницы (приложение читает их, а не query).
        cdp.call('Page.addScriptToEvaluateOnNewDocument', source=(
            "(()=>{try{localStorage.setItem('solo-theme','dark');"
            "localStorage.setItem('solo-start-page','welcome');"
            f"localStorage.setItem('solo-campaign','{campaign}');"
            "}catch(e){}})();"))
        cdp.call('Page.navigate',
                 url=f'http://127.0.0.1:{port}/?theme=dark&campaign={campaign}&t={time.time()}')
        assert cdp.wait_js(audit.WAIT_READY, 90), 'страница не готова'
        assert cdp.wait_js("!!window.soloMenu && !!document.querySelector('#nav-drawer')", 40)
        assert cdp.wait_js("document.body.classList.contains('campaign-open')", 20)
        cdp.eval(audit.setup_game(375))
        assert cdp.wait_js("!!document.querySelector('#play.view.active')", 15)
        yield audit, cdp
        cdp.close()
    finally:
        chrome.terminate()
        server.should_exit = True
        thread.join(10)
        sock.close()
        try:
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()


GEOMETRY = r"""
(() => {
  const px = v => Math.round(v * 10) / 10;
  const aside = document.querySelector('body > aside.sidebar');
  const drawer = document.querySelector('#nav-drawer');
  const scrim = document.querySelector('.m-scrim');
  const above = document.elementFromPoint(187, 60);
  const tabs = [...document.querySelectorAll('button.nav-tab')];
  const seen = e => e && e.getBoundingClientRect().height > 0;
  const exit = document.querySelector('aside.sidebar .nav-exit');
  const rows = [...document.querySelectorAll('#nav-drawer button.nav, #nav-drawer a.nav')]
    .filter(seen).filter(b => b !== exit);
  const lum = c => {
    const n = c.replace(/[^0-9.,]/g, '').split(',').slice(0, 3).map(Number);
    const lin = n.map(v => { const x = v / 255;
      return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
  };
  const ratio = (a, b) => { const la = lum(a), lb = lum(b);
    const hi = Math.max(la, lb), lo = Math.min(la, lb);
    return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100; };
  const sheetBg = aside ? getComputedStyle(aside).backgroundColor : null;
  return {
    viewport: innerHeight,
    open: document.body.classList.contains('mobile-menu-open'),
    sheet: aside ? {top: px(aside.getBoundingClientRect().top),
                    bottom: px(aside.getBoundingClientRect().bottom),
                    h: px(aside.getBoundingClientRect().height),
                    bg: sheetBg,
                    borderTop: getComputedStyle(aside).borderTopWidth,
                    radius: getComputedStyle(aside).borderTopLeftRadius} : null,
    above: above ? (above.className || above.tagName).toString() : null,
    scrimPointer: scrim ? getComputedStyle(scrim).pointerEvents : null,
    drawerOverflow: drawer ? Math.max(0, drawer.scrollHeight - drawer.clientHeight) : null,
    exit: exit ? {top: px(exit.getBoundingClientRect().top),
                  bottom: px(exit.getBoundingClientRect().bottom),
                  text: (exit.innerText || '').trim()} : null,
    gapListToExit: (exit && rows.length)
      ? px(exit.getBoundingClientRect().top - Math.max(...rows.map(b => b.getBoundingClientRect().bottom)))
      : null,
    tab: drawer ? drawer.dataset.tab : null,
    tabs: tabs.map(b => ({text: (b.textContent || '').trim(),
                          selected: b.getAttribute('aria-selected') === 'true',
                          color: getComputedStyle(b).color,
                          underline: getComputedStyle(b).borderBottomColor,
                          underlineWidth: getComputedStyle(b).borderBottomWidth,
                          border: getComputedStyle(b).borderTopWidth,
                          bg: getComputedStyle(b).backgroundColor})),
    contrast: {
      tabUnselectedText: tabs.length ? ratio(tabs[0].getAttribute('aria-selected') === 'true'
        ? getComputedStyle(tabs[1]).color : getComputedStyle(tabs[0]).color, sheetBg) : null,
      underline: sheetBg ? ratio('rgb(216, 160, 92)', sheetBg) : null,
    },
    groupLabelsVisible: [...document.querySelectorAll('#nav-drawer .nav-group-label')].filter(seen).length,
    rows: rows.map(b => (b.innerText || '').replace(/\s+/g, ' ').trim())
  };
})()
"""

EXIT = r"""
(() => {
  const d = document.querySelector('#exit-confirm');
  return {has: Boolean(d), open: Boolean(d && d.open), modal: d ? d.matches(':modal') : null,
          text: d ? (d.innerText || '').replace(/\s+/g, ' ').trim() : null,
          campaignOpen: document.body.classList.contains('campaign-open'),
          playActive: Boolean(document.querySelector('#play.view.active')),
          welcomeActive: Boolean(document.querySelector('#welcome.view.active'))};
})()
"""


def open_menu(audit, cdp, tab='campaign'):
    cdp.eval(audit.setup_menu(tab == 'app'))
    assert cdp.wait_js("document.body.classList.contains('mobile-menu-open')", 10)
    time.sleep(0.4)


def close_menu(cdp):
    cdp.eval("(()=>{const b=document.querySelector('.nav-head .nav-close');if(b)b.click();})()")
    time.sleep(0.3)


def test_drawer_is_a_sheet_and_closes_by_tap_on_the_dimmed_page(stand):
    """Замечания 1–3: лист от низа до 124–154 px сверху, тап выше закрывает,
    вкладки читаются парой, дубля названия капсом нет."""
    audit, cdp = stand
    for tab in ('campaign', 'app'):
        open_menu(audit, cdp, tab)
        state = cdp.eval(GEOMETRY)
        assert state['open'] is True
        # Лист прижат к низу и не обрывается на середине.
        assert state['sheet']['bottom'] == state['viewport'], state['sheet']
        # Сверху остаётся затемнённая страница — 124…154 px (SPEC §1).
        assert 124 <= state['sheet']['top'] <= 154, state['sheet']
        # Список помещается целиком: ни одна строка не уехала под «Выход».
        assert state['drawerOverflow'] == 0, state['drawerOverflow']
        # «Выход» — у нижнего края листа, зазор до списка больше прежних 5 px.
        assert state['exit']['bottom'] == state['viewport'], state['exit']
        assert state['gapListToExit'] >= 12, state['gapListToExit']
        # Вкладки: подчёркивание выбранной, подпись невыбранной ≥ 3:1, рамок нет.
        assert state['groupLabelsVisible'] == 0, 'дубль названия вкладки капсом'
        assert state['contrast']['tabUnselectedText'] >= 3, state['contrast']
        assert state['contrast']['underline'] >= 3, state['contrast']
        for tab_item in state['tabs']:
            assert tab_item['border'].startswith('0'), tab_item
            assert tab_item['bg'] == 'rgba(0, 0, 0, 0)', tab_item
        if tab == 'app':
            # Замечание 5: «Расходы» и «Скорость» названы по предмету.
            assert 'Расходы на Мастера' in state['rows'], state['rows']
            assert 'Скорость ответа' in state['rows'], state['rows']
            assert 'Расходы' not in state['rows'] and 'Скорость' not in state['rows'], state['rows']
        close_menu(cdp)

    # Тап выше листа попадает в затемнение, а не в живые кнопки полосы.
    open_menu(audit, cdp, 'campaign')
    assert cdp.eval(GEOMETRY)['above'] == 'm-scrim'
    for kind, buttons in (('mousePressed', 1), ('mouseReleased', 0)):
        cdp.call('Input.dispatchMouseEvent', type=kind, x=187, y=60, button='left',
                 clickCount=1, buttons=buttons)
    time.sleep(0.4)
    assert not cdp.eval("document.body.classList.contains('mobile-menu-open')")
    assert cdp.eval("(document.querySelector('body > main > .view.active')||{}).id") == 'play'


def test_exit_asks_and_cancel_keeps_the_campaign(stand):
    """Замечание 4: «Выход из игры» спрашивает; отказ не сбрасывает кампанию."""
    audit, cdp = stand
    open_menu(audit, cdp, 'campaign')
    before = cdp.eval(EXIT)
    assert before['has'] is True and before['open'] is False

    cdp.eval("(()=>{const b=document.querySelector('#nav-drawer [data-view=\"exit\"]');b.click();})()")
    time.sleep(0.4)
    asked = cdp.eval(EXIT)
    assert asked['open'] is True and asked['modal'] is True, asked
    assert 'Выйти из кампании?' in asked['text'], asked['text']
    assert 'Прогресс сохранён' in asked['text'], asked['text']
    # Вопрос не сбрасывает кампанию сам по себе.
    assert asked['campaignOpen'] is True and asked['playActive'] is True

    cdp.eval("(()=>{document.querySelector('#exit-confirm-no').click();})()")
    time.sleep(0.3)
    cancelled = cdp.eval(EXIT)
    assert cancelled['open'] is False
    assert cancelled['campaignOpen'] is True and cancelled['playActive'] is True, cancelled
    assert cancelled['welcomeActive'] is False

    # Согласие действительно выводит из кампании (диалог не «пустой»).
    cdp.eval("(()=>{document.querySelector('#nav-drawer [data-view=\"exit\"]').click();})()")
    time.sleep(0.4)
    cdp.eval("(()=>{document.querySelector('#exit-confirm-yes').click();})()")
    time.sleep(0.6)
    left = cdp.eval(EXIT)
    assert left['welcomeActive'] is True and left['campaignOpen'] is False, left


def test_note_above_the_system_bar_is_not_glued_to_it(stand):
    """Добавка оценщика: подпись «сохранено» под доком с отступом под полосу."""
    audit, cdp = stand
    gap = cdp.eval("""(()=>{
      const row=document.querySelector('#play .dice-row'), note=document.querySelector('#play .dice-row__note');
      if(!row||!note) return null;
      const s=getComputedStyle(row), r=row.getBoundingClientRect(), n=note.getBoundingClientRect();
      return {rowBottomGap: Math.round(innerHeight - r.bottom), rowHeight: Math.round(r.height),
              padding: s.paddingBottom, noteText: (note.textContent||'').trim(),
              noteBottomGap: (note.textContent||'').trim() ? Math.round(innerHeight - n.bottom) : null};
    })()""")
    assert gap is not None
    # Ряд прижат к низу кадра, а его нижний отступ и есть место под home-индикатор.
    assert gap['rowBottomGap'] <= 1, gap
    assert float(gap['padding'].split('px')[0]) >= 8, gap
    # Если подпись есть, она не упирается в системную полосу.
    assert gap['noteBottomGap'] is None or gap['noteBottomGap'] >= 8, gap
