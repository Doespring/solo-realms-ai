
import os, sys, time, json
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from test_dialogue_paging_api import MockLLM
import tempfile
tmp = Path(tempfile.mkdtemp())

app = create_app(tmp/'h.sqlite3', MockLLM())
c = TestClient(app)
b = c.get('/api/health').json()
print("HEALTH KEYS:", sorted(b.keys()))
print("drift=", b.get('drift'), "build=", b.get('build'), "disk=", b.get('disk'))

# timing of refresh path
from solo.build_identity import build_identity
t=time.perf_counter()
for _ in range(20): build_identity(refresh=True)
print("refresh avg ms", round((time.perf_counter()-t)/20*1000,3))
t=time.perf_counter()
for _ in range(20): build_identity(refresh=False)
print("cached avg ms", round((time.perf_counter()-t)/20*1000,3))

# what is covered?
ident = build_identity(refresh=True)
print("covered count", len(ident['covered']))
print("covered head", ident['covered'][:15])
print("'solo/app.py' in covered:", 'solo/app.py' in ident['covered'])
print("'solo/version.py' in covered:", 'solo/version.py' in ident['covered'])
