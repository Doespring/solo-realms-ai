"""r137 mutation check: without wizard.js in CHAT_ONLY_SCRIPTS the hero step dead-ends.

Starts the same app with CHAT_ONLY_SCRIPTS = ('play.js',), clicks the path in
Chromium, and reads what the player would see. App file untouched: the constant is
patched in memory only.
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

import solo.app as app_module  # noqa: E402
from solo.preview_mock import PreviewMock  # noqa: E402

PORT = 19322
BASE = f'http://127.0.0.1:{PORT}'
DATA = ROOT / 'runtime' / 'browser-begin-mutation'
DATA.mkdir(parents=True, exist_ok=True)
MODULES = Path('/home/hermes/.hermes/cache/scratch/pw/node_modules')
FIXTURE = ROOT / 'tests' / 'fixtures' / 'obsidian-scenario.md'
NODE = Path('/home/hermes/.hermes/profiles/coder/cache/scratch/pw/r137_mutation.js')

NODE_SCRIPT = r"""
const { chromium } = require('playwright');
const [base, fixture] = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1000, height: 800 } })).newPage();
  await p.goto(base + '/?ui=chat', { waitUntil: 'networkidle' });
  const out = { wizardScript: await p.locator('script[src*="wizard.js"]').count(),
                soloWizard: await p.evaluate(() => typeof window.soloWizard) };
  await p.click('#begin');
  await p.setInputFiles('#source-file', fixture);
  await p.waitForFunction(() => !document.getElementById('source-summary').hidden, null, { timeout: 20000 });
  await p.click('#source-next');
  await p.click('#hero-editor');
  await p.waitForTimeout(3000);
  out.heroStatus = await p.locator('#hero-status').innerText();
  out.wizardVisible = await p.locator('#wizard').count() ? await p.locator('#wizard').isVisible() : false;
  console.log('MUT_REPORT ' + JSON.stringify(out));
  await b.close();
})().catch(e => { console.error('MUT_FAIL ' + (e && e.message || e)); process.exit(1); });
"""


def main():
    original = app_module.CHAT_ONLY_SCRIPTS
    app_module.CHAT_ONLY_SCRIPTS = ('play.js',)
    try:
        app = app_module.create_app(DATA / 'solo.sqlite3', PreviewMock(), local_port=PORT,
                                    experimental=True, offline_mock=True)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=PORT, log_level='warning'))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(150):
            if server.started:
                break
            time.sleep(0.1)
        assert server.started
        NODE.write_text(NODE_SCRIPT, encoding='utf-8')
        env = dict(os.environ)
        env.setdefault('LD_LIBRARY_PATH', '/home/hermes/.local/chromium-libs/root/usr/lib/x86_64-linux-gnu')
        env['NODE_PATH'] = str(MODULES)
        proc = subprocess.run(['node', str(NODE), BASE, str(FIXTURE)], cwd=str(MODULES.parent),
                              capture_output=True, text=True, timeout=240, env=env)
        print(proc.stdout[-3000:])
        if proc.returncode:
            print('STDERR', proc.stderr[-1200:])
        print('EXIT', proc.returncode)
    finally:
        app_module.CHAT_ONLY_SCRIPTS = original
        print('constant restored:', app_module.CHAT_ONLY_SCRIPTS)


if __name__ == '__main__':
    main()
