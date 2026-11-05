
import os, subprocess, sys, time, hashlib
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
T = ROOT/'solo'/'chronicles.py'
raw = T.read_bytes()
print('len', len(raw), 'sha', hashlib.sha256(raw).hexdigest()[:12])
txt = raw.decode('utf-8')
print('repr tail:', repr(txt[-120:]))
print('crlf?', b'\r\n' in raw)
