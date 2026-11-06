
import os, sys, time
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
T = ROOT/'solo'/'chronicles.py'
orig = T.read_bytes()
CASES = {
 'plain marker          ': b"# MARKER_AAA\n",
 'insert word only      ': b"# sys.path.insert commented\n",
 'insert real statement ': b"\nimport sys as _s\n_s.path.insert(0, r'C:/elsewhere')\n",
}
for name, body in CASES.items():
    T.write_bytes(orig + body)
    raw = T.read_bytes()
    print(f'{name} wrote={len(orig+body)} ondisk={len(raw)} kept={raw==orig+body}')
    T.write_bytes(orig)
print()
print('conclusion check: appended bytes at end of disk file after each write:')
T.write_bytes(orig + CASES['insert real statement '])
raw=T.read_bytes(); print(repr(raw[-200:]))
T.write_bytes(orig)
