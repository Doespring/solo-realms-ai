
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

# Which solo modules get imported when app is created?
import solo
def snap(): return set(n for n in sys.modules if n=='solo' or n.startswith('solo.'))
before = snap()
app = create_app(tmp/'a.sqlite3', MockLLM())
after = snap()
print("modules after create_app:", len(after))
from pathlib import Path as P
all_py = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT/'solo').glob('*.py'))
print("solo/*.py files on disk:", len(all_py))
from solo.build_identity import build_identity
cov = set(build_identity(refresh=True)['covered'])
names = {n for n in after}
missing = [p for p in all_py if p.replace('/','.')[:-3] not in names]
print("NOT imported at app creation:", len(missing))
print(missing[:60])
# nat20_api specifically
import solo.nat20_api
m = sys.modules['solo.nat20_api']
print("nat20_api file:", getattr(m,'__file__',None))
print("in covered now?", 'solo.nat20_api' in set(build_identity(refresh=True)['covered']))
