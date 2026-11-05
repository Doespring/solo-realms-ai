
import os, sys, time, json, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests")
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from test_dialogue_paging_api import MockLLM

ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
tmp = Path(tempfile.mkdtemp())

def probe(label, rel):
    target = ROOT/rel
    original = target.read_bytes()
    app = create_app(tmp/(label+'.sqlite3'), MockLLM())
    c = TestClient(app)
    b0 = c.get('/api/health').json()
    try:
        target.write_bytes(original + b'\n# probe\n')
        b1 = c.get('/api/health').json()
    finally:
        target.write_bytes(original)
    b2 = c.get('/api/health').json()
    print(f"{label:35s} {rel:28s} before drift={b0['drift']}  after drift={b1['drift']}  build={b1['build']} disk={b1['disk']}  restored drift={b2['drift']}")

# 1. module actually imported & covered
probe("chronicles"(0) if False else "chronicles", "solo/chronicles.py")
# 2. module NOT imported by this app / not in covered
for rel in ["solo/adventure_library.py", "solo/nat20_api.py", "solo/preparation.py", "solo/version.py", "solo/build_identity.py"]:
    try:
        probe("x", rel)
    except Exception as e:
        print(rel, "ERR", e)
# 3. UI files: web/play.js is hashed into 'ui', NOT into 'build'
from solo.build_identity import build_identity
ident = build_identity(refresh=True)
print("ui digest field:", ident.get('ui'), "build:", ident.get('build'))
target = ROOT/'web'/'play.js'
o = target.read_bytes()
try:
    target.write_bytes(o + b'\n// probe\n')
    m = build_identity(refresh=True)
    print("after editing web/play.js: drift=", m['drift'], "build=", m['build'], "disk=", m['disk'], "ui=", m['ui'])
finally:
    target.write_bytes(o)
# 4. What about whole-package tampering: a new file added inside solo/ that is not imported
