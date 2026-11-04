
import os, sys, json, tempfile, shutil, importlib
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from test_dialogue_paging_api import MockLLM

tmp = Path(tempfile.mkdtemp())
api = TestClient(create_app(tmp/'h.sqlite3', MockLLM()), raise_server_exceptions=False)
d = api.get('/api/health').json()
print("health keys:", sorted(d.keys()))
print("ui_disk present in health?", 'ui_disk' in d, "| value:", d.get('ui_disk'))
from solo.build_identity import build_identity
bi = build_identity(refresh=True)
print()
print("build_identity(refresh=True) keys:", sorted(bi.keys()))
print("  disk:", bi.get('disk'), "ui:", bi.get('ui'), "ui_disk:", bi.get('ui_disk'), "drift:", bi.get('drift'), "ui_drift:", bi.get('ui_drift'))
print()
# A DELETED UI file: the start digest already counted it, refresh() cannot read it
web = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")/'web'
victim = web/'obsidian.css'
backup = tmp/'obsidian.css.bak'
shutil.copy2(victim, backup)
try:
    victim.unlink()
    r = api.get('/api/health')
    print("health with a DELETED web/obsidian.css:", r.status_code, r.text[:200])
    try:
        build_identity(refresh=True)
    except Exception as e:
        print("build_identity(refresh=True) raised:", type(e).__name__, e)
finally:
    shutil.copy2(backup, victim)
print("restored; health:", api.get('/api/health').status_code, "drift:", api.get('/api/health').json().get('drift'))
