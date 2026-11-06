
import os, sys, importlib
os.environ['SOLO_TEST']='1'
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
T = ROOT/'solo'/'chronicles.py'
orig = T.read_bytes()
T.write_bytes(orig + b"\nimport sys as _s\n_s.path.insert(0, r'C:/elsewhere')\n")
try:
    print('on disk contains insert ->', b'sys.path.insert' in T.read_bytes())
    # replicate the test module's own logic in THIS process
    mod = importlib.import_module('tests.test_copy_does_not_reach_back') if (ROOT/'tests'/'__init__.py').exists() else None
    dp = ROOT/'tests'/'test_copy_does_not_reach_back.py'
    text = dp.read_text(encoding='utf-8', errors='replace')
    print('test file defines sources() ->', 'def sources()' in text)
    import re
    m = re.search(r"SOLO\s*=\s*(.+)", text)
    print('SOLO line ->', m.group(0).strip())
finally:
    T.write_bytes(orig)
print('restored', T.read_bytes()==orig)
