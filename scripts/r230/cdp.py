"""Minimal CDP driver for r230 — own headless Chromium, own websocket.

Run it with the harness virtualenv (it has `websockets`):

    /home/hermes/.hermes/installs/019d0d114d22b7e6/environments/<env>/venv/bin/python cdp.py

Why not the browser_exec tool: its default CDP call timeout is 5 s and the game
screen blocks the main thread longer than that while it renders, which killed
the session mid-run. Here every call waits as long as it needs.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

from websockets.sync.client import connect

CHROME = '/home/hermes/.hermes/tools/chromium-1208/chrome-linux64/chrome'
PORT = 9335
WORKSPACE = '/home/hermes/.hermes/kanban/workspaces/t_749e419f'
PROFILE = os.path.join(WORKSPACE, 'chrome-profile')


class Chromium:
    def __init__(self, port=PORT, profile=PROFILE, size=(390, 844)):
        self.port = port
        self.profile = profile
        self.proc = None
        self.ws = None
        self.sid = None
        self.msg_id = 0
        self.size = size

    # ---------- process ----------
    def start(self):
        if os.path.isdir(self.profile):
            shutil.rmtree(self.profile, ignore_errors=True)
        os.makedirs(self.profile, exist_ok=True)
        args = [CHROME, '--headless=new', '--no-sandbox', '--disable-gpu',
                '--hide-scrollbars', '--force-color-profile=srgb', '--no-first-run',
                '--no-default-browser-check', '--disable-extensions',
                f'--remote-debugging-port={self.port}',
                f'--user-data-dir={self.profile}',
                f'--window-size={self.size[0]},{self.size[1]}',
                '--ozone-platform=headless', 'about:blank']
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/json/version', timeout=2) as r:
                    info = json.load(r)
                break
            except Exception:
                time.sleep(0.3)
        else:
            raise RuntimeError('chrome did not start')
        self.ws = connect(info['webSocketDebuggerUrl'], max_size=64 * 1024 * 1024)
        # attach to the first page target
        targets = self.send('Target.getTargets')['targetInfos']
        page = next(t for t in targets if t['type'] == 'page')
        self.sid = self.send('Target.attachToTarget', targetId=page['targetId'], flatten=True)['sessionId']
        return self

    def stop(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()

    # ---------- transport ----------
    def send(self, method, **params):
        self.msg_id += 1
        mid = self.msg_id
        payload = {'id': mid, 'method': method, 'params': params}
        if self.sid and not method.startswith('Target.'):
            payload['sessionId'] = self.sid
        self.ws.send(json.dumps(payload))
        while True:
            raw = self.ws.recv()
            msg = json.loads(raw)
            if msg.get('id') == mid:
                if 'error' in msg:
                    raise RuntimeError(f'{method}: {msg["error"]}')
                return msg.get('result', {})
            # ignore events

    def eval(self, expression, timeout=60):
        res = self.send('Runtime.evaluate', expression=expression, returnByValue=True,
                        awaitPromise=True, timeout=timeout * 1000)
        if res.get('exceptionDetails'):
            raise RuntimeError('JS error: ' + json.dumps(res['exceptionDetails'])[:600])
        return res.get('result', {}).get('value')

    # ---------- page ----------
    def viewport(self, w, h, dpr=2):
        self.size = (w, h)
        self.send('Emulation.setDeviceMetricsOverride', width=w, height=h,
                  deviceScaleFactor=dpr, mobile=True,
                  screenWidth=w, screenHeight=h)
        self.send('Emulation.setTouchEmulationEnabled', enabled=True, maxTouchPoints=5)

    def _wait_ready(self, marker=None, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.eval('document.readyState') == 'complete':
                    if marker is None or self.eval(marker):
                        return True
            except Exception:
                pass
            time.sleep(0.3)
        raise TimeoutError('document did not become ready')

    def goto(self, url, wait=1.5):
        self.send('Page.enable')
        self.send('Page.navigate', url=url)
        self._wait_ready("location.href.startsWith(%r)" % url)
        time.sleep(wait)

    def reload(self, wait=2.0):
        self.send('Page.enable')
        self.send('Page.reload', ignoreCache=True)
        time.sleep(0.8)  # let the navigation commit before polling the new context
        self._wait_ready("location.protocol.startsWith('http') && document.querySelectorAll('section.view').length > 5")
        time.sleep(wait)

    def shot(self, path, full=False):
        shutil.os.makedirs(os.path.dirname(path), exist_ok=True)
        params = {'format': 'png', 'fromSurface': True, 'captureBeyondViewport': full}
        res = self.send('Page.captureScreenshot', **params)
        import base64
        with open(path, 'wb') as fh:
            fh.write(base64.b64decode(res['data']))
        return path


def wait_for(cdp, expression, timeout=30, poll=0.25):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = cdp.eval(expression)
            if last:
                return last
        except Exception as exc:
            last = str(exc)
        time.sleep(poll)
    raise TimeoutError(f'{expression} — last {last!r}')
