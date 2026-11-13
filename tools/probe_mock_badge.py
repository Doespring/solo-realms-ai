#!/usr/bin/env python3
"""n12 — проверка метки «Тестовые данные · mock» на 1440.

Что измеряет:
  • обычный запуск (без флага): в шапке НЕТ «Тестовые данные», в тексте DOM
    нет «mock» — тестовый адаптер показан как «Подключите облачного Мастера»;
  • тестовый запуск (?audit=1): метка «Тестовые данные · mock» есть.

По умолчанию поднимает СВОЙ инстанс приложения на свободном порту с тестовым
адаптером (`tools/ui_audit.py --serve`, свой `--data-dir`, порт 19215 и живой
профиль владельца не трогаются), потому что именно этот стенд дал кадры, где
владелец видел «заглушку». Режим `--base` меряет уже поднятый стенд.

Запуск:
    .venv/bin/python tools/probe_mock_badge.py                 # свой стенд
    .venv/bin/python tools/probe_mock_badge.py --base http://127.0.0.1:19215
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import ui_audit as ua  # noqa: E402 — переиспользуем CDP-клиент и запуск chromium

WIDTH, HEIGHT = 1440, 900

STATUS_JS = """(() => {
  const node = document.querySelector('#model-status');
  const header = document.querySelector('header');
  const notice = document.querySelector('#creation-mock-notice');
  const noticeShown = !!(notice && notice.getBoundingClientRect().width > 0.5);
  // Видимый текст с «mock» — по листовым узлам, у которых есть геометрия:
  // hidden-блоки в innerText не попадают, а textContent попадает.
  const leaves = [...document.querySelectorAll('body *')]
    .filter(n => !n.children.length && n.getClientRects().length);
  const rows = nodes => nodes.filter(n => /mock/i.test(n.textContent || ''))
    .map(n => (n.id || n.className || n.tagName) + ': ' + n.textContent.trim().slice(0, 80))
    .slice(0, 6);
  const visibleMock = rows(leaves.filter(n => !n.closest('#creation-mock-notice')));
  // Строки про тестовый (mock) Мастер принадлежат mock-режиму подготовки книги.
  const noticeMock = noticeShown ? rows(leaves.filter(n => n.closest('#creation-mock-notice'))) : [];
  return {
    modelStatus: node ? node.textContent : null,
    modelStatusVisible: node ? !!(node.getBoundingClientRect().width > 0.5) : false,
    headerText: header ? header.innerText.replace(/\\s+/g, ' ').trim() : null,
    mockNoticeVisible: noticeShown,
    visibleMock,
    noticeMock,
    bodyHasMock: /mock/i.test(document.body.innerText),
    bodyHasTestData: document.body.innerText.includes('Тестовые данные'),
  };
})()"""


def wait_health(base: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = urllib.request.urlopen(base + '/api/health', timeout=3).read()
            return json.loads(raw)
        except Exception:  # noqa: BLE001 — стенд может ещё подниматься
            time.sleep(0.25)
    raise SystemExit('стенд не ответил: ' + base + '/api/health')


def start_mock_server(data_dir: Path) -> tuple[subprocess.Popen, str]:
    """Стенд аудита: тестовый адаптер, свой data-dir, свободный порт."""
    data_dir.mkdir(parents=True, exist_ok=True)
    port = ua.free_port()
    info = data_dir / 'server.json'
    if info.exists():
        info.unlink()
    log = open(data_dir / 'probe-server.log', 'wb')
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / 'tools/ui_audit.py'), '--serve',
         '--data-dir', str(data_dir), '--port', str(port)],
        stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))
    base = f'http://127.0.0.1:{port}'
    wait_health(base)
    return proc, base


def start_ordinary_server(data_dir: Path) -> tuple[subprocess.Popen, str]:
    """Обычная сборка: тот же вход, что у стенда владельца (serve_solo.py),
    только свой data-dir и порт. Облачный адаптер без ключей сеть не трогает."""
    data_dir.mkdir(parents=True, exist_ok=True)
    port = ua.free_port()
    # `prepare_profile` берёт только новый пустой каталог: профиль кладём в
    # подкаталог, а лог — рядом, чтобы каталог профиля остался нетронутым.
    profile = data_dir / 'profile'
    log = open(data_dir / 'probe-server.log', 'wb')
    proc = subprocess.Popen(
        [sys.executable, str(Path.home() / '.hermes/scripts/serve_solo.py'),
         '--root', str(ROOT), '--data-dir', str(profile), '--port', str(port)],
        stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))
    base = f'http://127.0.0.1:{port}'
    wait_health(base, 240)
    return proc, base


def shoot(cdp, base: str, suffix: str, name: str, out_dir: Path, screen: str = 'new') -> dict:
    cdp.call('Emulation.setDeviceMetricsOverride', width=WIDTH, height=HEIGHT,
             deviceScaleFactor=1, mobile=False)
    url = base + '/' + suffix
    cdp.call('Page.navigate', url=url)
    # readyState 'complete' ждёт все картинки; приложение готово раньше — как
    # только объявило view() и шапку.
    if not cdp.wait_js("document.readyState !== 'loading' && typeof view === 'function'"
                       " && !!document.querySelector('.runtime')", 60):
        raise SystemExit('страница не догрузилась: ' + url)
    # Главная собирается скриптами (obsidian.js) после первого кадра: пока
    # `soloOpenPreparation` не объявлен, клик по «Начать историю» уходит в
    # пустоту — так первый заход и снял не тот экран.
    if not cdp.wait_js("!!document.querySelector('#welcome.view.active')"
                       " && typeof window.soloOpenPreparation === 'function'", 40):
        raise SystemExit('главная не собралась: ' + url)
    if screen == 'new':
        # Кадр, на котором критик №2 видел метку: экран создания книги.
        # Открываем тем же путём игрока, что и карточка черновика, —
        # `soloOpenCreation` из chronicles.js. Кнопка «Начать историю» на этом
        # стенде не годится: подготовка и создание перекрывают друг у друга
        # `soloOpenPreparation`, и клик уводит на экран подготовки.
        if not cdp.wait_js("typeof window.soloOpenCreation === 'function'", 30):
            raise SystemExit('создание книги не объявлено: ' + url)
        cdp.eval("window.soloOpenCreation()")
        if not cdp.wait_js("!!document.querySelector('#creation.view.active')", 30):
            raise SystemExit('экран создания не открылся: ' + url)
        cdp.wait_js("!!document.querySelector('#creation-description')", 15)
        time.sleep(1.2)
    # Метку ставит ответ /health (опрос раз в 5 с), ждём именно его.
    cdp.wait_js("(() => { const n = document.querySelector('#model-status');"
                "return !!n && n.textContent.trim() !== 'Проверяем Мастера…'; })()", 20)
    time.sleep(1.0)  # цикл health может переписать текст ещё раз
    data = cdp.eval(STATUS_JS) or {}
    shot = cdp.call('Page.captureScreenshot', format='png')
    (out_dir / name).write_bytes(base64.b64decode(shot['data']))
    data.update(url=url, file=str(out_dir / name), screen=screen)
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='', help='уже поднятый стенд; иначе свой')
    parser.add_argument('--screen', default='', choices=['', 'new', 'home'],
                        help='кадр: new — экран создания (по умолчанию на своём стенде), '
                             'home — главная (по умолчанию на чужом стенде)')
    parser.add_argument('--stand', default='mock', choices=['mock', 'plain'],
                        help='mock — стенд аудита с тестовым адаптером; '
                             'plain — обычная сборка через serve_solo.py')
    parser.add_argument('--data-dir', default=str(ROOT / 'runtime/n12-badge'))
    parser.add_argument('--out', default=str(ROOT / 'reports/n12'))
    parser.add_argument('--chrome-dir', default=str(ROOT / 'runtime/n12-badge/chrome'))
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    screen = args.screen or ('home' if args.base else 'new')

    proc = None
    if args.base:
        base = args.base.rstrip('/')
    elif args.stand == 'plain':
        proc, base = start_ordinary_server(Path(args.data_dir).resolve() / 'plain')
    else:
        proc, base = start_mock_server(Path(args.data_dir).resolve())

    health = wait_health(base)
    chrome = None
    try:
        chrome, chrome_port = ua.start_chrome(Path(args.chrome_dir).resolve())
        cdp = ua.CDP(ua.page_target(chrome_port))
        cdp.call('Page.enable')
        cdp.call('Runtime.enable')
        cdp.call('Network.enable')
        # Тот же вход, что у аудита: без этого Chromium открывает последнюю
        # кампанию, а кадр «создание» снимает не тот экран.
        cdp.call('Network.setCacheDisabled', cacheDisabled=True)
        cdp.call('Page.addScriptToEvaluateOnNewDocument', source=(
            "(()=>{try{const q=new URLSearchParams(location.search);"
            "const t=q.get('theme');if(t)localStorage.setItem('solo-theme',t);"
            "localStorage.setItem('solo-start-page','welcome');}catch(e){}})();"))
        result = {
            'base': base,
            'healthLlm': health.get('llm'),
            'healthLlmMock': bool((health.get('llm') or {}).get('mock')),
            'commit': health.get('commit'),
            'frames': {},
        }
        result['frames']['plain-home'] = shoot(
            cdp, base, '?theme=dark', 'n12-plain-home-1440.png', out_dir, 'home')
        result['frames']['plain-new'] = shoot(
            cdp, base, '?theme=dark', 'n12-plain-creation-1440.png', out_dir, 'new')
        # Тот же кадр, как его снимает аудит: в URL уже есть непустой audit=.
        result['frames']['audit-frame'] = shoot(
            cdp, base, '?theme=dark&audit=game-1440', 'n12-audit-frame-1440.png', out_dir, 'new')
        result['frames']['flagged'] = shoot(
            cdp, base, '?theme=dark&audit=1', 'n12-flagged-1440.png', out_dir, 'new')
        cdp.close()
        badge = 'Тестовые данные · mock'

        def clean(frame):
            """В кадре нет метки: ни в шапке, ни видимым текстом «mock»."""
            return (badge not in (frame['modelStatus'] or '')
                    and not frame['bodyHasTestData']
                    and not frame['visibleMock'])

        home = result['frames']['plain-home']
        checks = {
            'plain_home_no_badge': clean(home),
            # На главной нет и слова «mock» в тексте: там нет ни метки, ни
            # служебных подписей (заглушка «mock-Мастер» живёт на экране
            # подготовки книги — это mock-режим подготовки из n11, его не трогаем).
            'plain_home_no_mock_word': not home['bodyHasMock'],
            'plain_creation_no_badge': clean(result['frames']['plain-new']),
        }
        if result['healthLlmMock']:
            checks['flagged_has_badge'] = badge in (result['frames']['flagged']['modelStatus'] or '')
        else:
            checks['flagged_no_badge'] = all(clean(f) for f in result['frames'].values())
        result['checks'] = checks
        result['ok'] = all(checks.values())
        (out_dir / 'n12-measures.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result['ok'] else 1
    finally:
        if chrome is not None:
            chrome.terminate()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                proc.kill()


if __name__ == '__main__':
    sys.exit(main())
