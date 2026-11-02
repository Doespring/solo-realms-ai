
import os, sys, json, shutil, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.build_identity import build_identity, _ui_files
from test_dialogue_paging_api import MockLLM

tmp = Path(tempfile.mkdtemp())
api = TestClient(create_app(tmp/'h.sqlite3', MockLLM()))

def h(tag):
    d = api.get('/api/health').json()
    print(tag, {k: d.get(k) for k in ('build','disk','drift','ui','ui_drift','ui_disk')})
    return d

h('baseline           ')
print()
# 1) a fully normal, untouched server, called many times
for _ in range(3): api.get('/api/health')
h('after 3 more calls ')
print()
# 2) a temporary editor swap file appears in web/ (VS Code, vim, etc.)
web = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")/'web'
backup = web/'index.html.swp'
backup.write_bytes(b'vim swap')
try:
    h('web/index.html.swp ')
finally:
    backup.unlink()
h('after swap removed ')
print()
# 3) a NEW real UI file is added
newf = web/'probe-extra.js'
newf.write_text('// new module\n', encoding='utf-8')
try:
    h('new web/probe-extra.js')
finally:
    newf.unlink()
h('after removal      ')
print()
# 4) a file that exists at start but disappears later -> what happens to the reader?
victim = web/'probe-temp.js'
victim.write_text('// temp\n', encoding='utf-8')
print("ui list contains temp:", 'web/probe-temp.js' in [str(p) for p in _ui_files()])
victim.unlink()
try:
    build_identity(refresh=True)
    print("refresh after a listed file vanished: OK")
except Exception as e:
    print("refresh after a listed file vanished raised:", type(e).__name__, e)
try:
    d = api.get('/api/health')
    print("health after vanished file:", d.status_code)
except Exception as e:
    print("health after vanished file raised:", type(e).__name__, e)
