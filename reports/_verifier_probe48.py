import os, sys, tempfile
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r'C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI')
sys.path.insert(0, r'C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/tests')
from pathlib import Path
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.build_identity import build_identity
from test_dialogue_paging_api import MockLLM

tmp = Path(tempfile.mkdtemp())
TestClient(create_app(tmp/'h.sqlite3', MockLLM())).get('/api/health')
bi = build_identity(refresh=True)
covered = bi['covered']
print('covered entries:', len(covered), '| sample:', covered[:3])
print('any covered entry looks like a path?', any('/' in c or chr(92) in c for c in covered))
root = Path(r'C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI')
on_disk = {str(p.relative_to(root)).replace(chr(92), '/') for p in (root/'solo').rglob('*.py')}
print('solo/*.py on disk:', len(on_disk))
covered_as_paths = set()
for name in covered:
    try:
        mod = sys.modules.get(name)
        f = getattr(mod, '__file__', None)
        if f: covered_as_paths.add(str(Path(f).resolve().relative_to(root)).replace(chr(92), '/'))
    except Exception: pass
print('covered resolvable to paths:', len(covered_as_paths))
print('files never checked:', len(on_disk - covered_as_paths))
print('  examples:', sorted(on_disk - covered_as_paths)[:8])
