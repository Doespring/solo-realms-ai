"""The one place that decides whether a real browser can run here.

Every UI test needs the same answer, and the answer must be measurable rather
than assumed. A missing browser and a browser the sandbox refuses to start are
different facts: the first is a setup problem, the second is an environment
limit that no amount of installing fixes. This module tells them apart by
actually trying to launch, then remembers the outcome for the whole run so the
check costs one attempt, not one per test.
"""
import functools
import os
import shutil
import subprocess
from pathlib import Path

LAUNCH_PROBE = ("const{chromium}=require(process.env.PW);"
                "chromium.launch({channel:'msedge',headless:true})"
                ".then(b=>b.close().then(()=>{console.log('OK')}))"
                ".catch(e=>{console.log('ERR:'+String(e.message).split(String.fromCharCode(10))[0]);"
                "process.exit(9)})")


def _runtime():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('SOLO_UI_NODE') or shutil.which('node') or runtime / 'bin/node.exe'
    playwright = os.environ.get('SOLO_UI_PLAYWRIGHT') or runtime / 'node_modules/playwright'
    return node, playwright


@functools.lru_cache(maxsize=1)
def blocker() -> str:
    '''Return why a real browser cannot run here, or an empty string if it can.'''
    node, playwright = _runtime()
    if not Path(node).is_file():
        return 'Node runtime not installed: %s' % node
    if not Path(playwright).exists():
        return 'Playwright not installed: %s' % playwright
    try:
        done = subprocess.run([str(node), '-e', LAUNCH_PROBE],
                              env={**os.environ, 'PW': str(playwright)},
                              capture_output=True, text=True, encoding='utf-8', timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        return 'browser launch could not be attempted: %s' % error
    out = (done.stdout or '').strip()
    if done.returncode == 0 and out == 'OK':
        return ''
    return 'browser cannot start in this environment: %s' % (out or done.stderr.strip()[:200])


def require_browser():
    '''Skip with the measured reason; never let a UI test pass without a browser.'''
    reason = blocker()
    if reason:
        import pytest
        pytest.skip('BLOCKED BY ENVIRONMENT: ' + reason)

