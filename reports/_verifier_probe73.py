
import time, hashlib, os
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
T = ROOT/'solo'/'chronicles.py'
print('python version', __import__('sys').version)
print('env PYTHONWATCHDOG', os.environ.get('PYTHONWATCHDOG'))
orig = T.read_bytes()
print('before len', len(orig))
T.write_bytes(orig + b"# MARKER_XYZ\n")
for i in range(6):
    raw = T.read_bytes()
    print(f'  +{i*0.05:.2f}s len={len(raw)} marker={b"MARKER_XYZ" in raw} sha={hashlib.sha256(raw).hexdigest()[:10]}')
    time.sleep(0.05)
T.write_bytes(orig)
print('restored', T.read_bytes()==orig)
