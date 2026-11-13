"""r137 live run: «Начать приключение» → источник → «что понято» → редактор героя.

Поднимает приложение с экспериментальными маршрутами Nat20 (редактор героя) на
офлайн-моке, открывает ?ui=chat в настоящем Chromium и проходит путь кликами.
0 платных вызовов.

    . ~/.local/chromium-libs/env.sh
    cd /home/hermes/projects/SoloRealmsAI-Nat20
    SOLO_TEST=1 .venv/bin/python /home/hermes/.hermes/profiles/coder/cache/scratch/pw/r137_begin_path.py
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path('/home/hermes/projects/SoloRealmsAI-Nat20')
sys.path.insert(0, str(ROOT))
os.environ.setdefault('SOLO_TEST', '1')

import uvicorn  # noqa: E402

from solo.app import create_app  # noqa: E402
from solo.preview_mock import PreviewMock  # noqa: E402

PORT = 19321
BASE = f'http://127.0.0.1:{PORT}'
DATA = ROOT / 'runtime' / 'browser-begin'
DATA.mkdir(parents=True, exist_ok=True)
OUT = Path('/home/hermes/.hermes/profiles/coder/cache/scratch/pw')
NODE = OUT / 'r137_begin.js'
MODULES = Path('/home/hermes/.hermes/cache/scratch/pw/node_modules')
FIXTURE = ROOT / 'tests' / 'fixtures' / 'obsidian-scenario.md'
BAD = DATA / 'scan.docx'
BAD.write_bytes(b'PK\x03\x04 this is not a scenario the reader accepts')
NOTEXT = DATA / 'scan.txt'
NOTEXT.write_bytes(b'12 34 56 !!! ###')
BIG = DATA / 'huge.md'
BIG.write_bytes(b'# x\n' * (3 * 1024 * 1024))  # 12 МБ

NODE_SCRIPT = r"""
const { chromium } = require('playwright');
const [base, fixture, bad, big, notext, outDir] = process.argv.slice(2);
(async () => {
  const report = { steps: [], errors: [], ok: false };
  const step = (n, v) => { report.steps.push({ name: n, ...v }); console.log('NODE', n, JSON.stringify(v).slice(0, 600)); };
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1100, height: 900 } });
  const p = await ctx.newPage();
  const consoleErrors = [];
  p.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
  p.on('pageerror', e => consoleErrors.push('pageerror: ' + String(e).slice(0, 200)));
  await p.goto(base + '/?ui=chat', { waitUntil: 'networkidle' });
  step('loaded', { title: await p.title(), scripts: await p.locator('script[src]').count() });
  step('editor_script', { wizard: await p.locator('script[src*="wizard.js"]').count() });

  await p.click('#begin');
  await p.waitForTimeout(300);
  step('dialog', {
    open: await p.locator('#create-dialog').evaluate(n => n.open),
    fileInput: await p.locator('#source-file').count(),
    manualHidden: await p.locator('#manual-sheet').evaluate(n => n.hidden),
    visibleButtons: await p.locator('#create-dialog button:visible').allInnerTexts(),
    hint: (await p.locator('#begin-source .hint').first().innerText()).slice(0, 120),
  });

  // 1. Обычный файл: что понято.
  await p.setInputFiles('#source-file', fixture);
  await p.waitForFunction(() => !document.getElementById('source-summary').hidden, null, { timeout: 30000 });
  step('understood', {
    status: await p.locator('#source-status').innerText(),
    summary: (await p.locator('#source-summary').innerText()).split('\n').filter(Boolean),
    nextVisible: await p.locator('#source-next').isVisible(),
    dialogOpen: await p.locator('#create-dialog').evaluate(n => n.open),
  });
  await p.screenshot({ path: outDir + '/r137-understood.png' });

  // 2. Переход к герою и редактор.
  await p.click('#source-next');
  await p.waitForTimeout(200);
  step('hero_step', {
    heroVisible: await p.locator('#begin-hero').isVisible(),
    note: (await p.locator('#hero-note').innerText()).slice(0, 140),
  });
  await p.click('#hero-editor');
  await p.waitForSelector('#wizard:not([hidden])', { timeout: 60000 });
  await p.waitForTimeout(2500);
  step('editor', {
    visible: await p.locator('#wizard').isVisible(),
    title: await p.locator('#wizard-title').innerText(),
    steps: await p.locator('.wizard-steps button').allInnerTexts(),
    stage: (await p.locator('#wizard .wizard-stage').innerText()).slice(0, 400),
    heroStatus: await p.locator('#hero-status').innerText(),
    dialogOpen: await p.locator('#create-dialog').evaluate(n => n.open),
  });
  await p.screenshot({ path: outDir + '/r137-editor.png' });
  await p.click('#wizard .wizard-close').catch(() => {});
  await p.waitForTimeout(400);

  // 3. Прощение: чужой формат — понятная фраза, диалог не перезапускается.
  await p.click('#begin');
  await p.waitForTimeout(200);
  await p.setInputFiles('#source-file', bad);
  await p.waitForTimeout(1500);
  step('refused_format', {
    status: await p.locator('#source-status').innerText(),
    dialogOpen: await p.locator('#create-dialog').evaluate(n => n.open),
    sourceStepVisible: await p.locator('#begin-source').isVisible(),
    nextVisible: await p.locator('#source-next').isVisible(),
  });

  // 4. Прощение: больше 8 МБ — отказ до отправки, выбор другого файла остаётся.
  await p.setInputFiles('#source-file', big);
  await p.waitForTimeout(1200);
  step('refused_size', {
    status: await p.locator('#source-status').innerText(),
    dialogOpen: await p.locator('#create-dialog').evaluate(n => n.open),
  });

  // 5. Файл без текста: сервер отвечает 200, но подготовка по нему запрещена.
  await p.setInputFiles('#source-file', notext);
  await p.waitForTimeout(2500);
  step('stored_but_unreadable', {
    status: await p.locator('#source-status').innerText(),
    nextVisible: await p.locator('#source-next').isVisible(),
    summary: (await p.locator('#source-summary').innerText()).split('\n').filter(Boolean).slice(0, 3),
  });

  // 6. Второй заход после отказа: обычный файл снова принимается в том же диалоге.
  await p.setInputFiles('#source-file', fixture);
  await p.waitForTimeout(2500);
  step('retry_after_refusal', {
    status: await p.locator('#source-status').innerText(),
    sourceStepVisible: await p.locator('#begin-source').isVisible(),
    summaryVisible: await p.locator('#source-summary').isVisible(),
    summary: (await p.locator('#source-summary').innerText()).split('\n').filter(Boolean),
  });
  await p.screenshot({ path: outDir + '/r137-refusal-retry.png' });

  report.consoleErrors = consoleErrors;
  report.ok = true;
  await b.close();
  console.log('REPORT_JSON ' + JSON.stringify(report));
})().catch(e => { console.error('NODE_FAIL ' + (e && e.message || e)); process.exit(1); });
"""


def main():
    from solo import nat20_api
    nat20_api.secrets.randbits = lambda bits: 5
    app = create_app(DATA / 'solo.sqlite3', PreviewMock(), local_port=PORT, experimental=True, offline_mock=True)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=PORT, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(150):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, 'сервер не поднялся'
    print('STEP server', PORT, flush=True)

    NODE.write_text(NODE_SCRIPT, encoding='utf-8')
    env = dict(os.environ)
    env.setdefault('LD_LIBRARY_PATH', '/home/hermes/.local/chromium-libs/root/usr/lib/x86_64-linux-gnu')
    # playwright + its browsers live in the shared scratch from the previous live runs
    env['NODE_PATH'] = str(MODULES)
    proc = subprocess.run(['node', str(NODE), BASE, str(FIXTURE), str(BAD), str(BIG), str(NOTEXT), str(OUT)],
                          cwd=str(MODULES.parent), capture_output=True, text=True, timeout=300, env=env)
    print(proc.stdout[-6000:])
    if proc.returncode != 0:
        print('NODE_STDERR', proc.stderr[-2000:])
    for line in proc.stdout.splitlines():
        if line.startswith('REPORT_JSON '):
            report = json.loads(line[len('REPORT_JSON '):])
            (OUT / 'r137-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print('RESULT ok=%s consoleErrors=%s' % (report.get('ok'), report.get('consoleErrors')[:4]))
    print('EXIT', proc.returncode)


if __name__ == '__main__':
    main()
