
import os
os.environ['SOLO_TEST']='1'
from pathlib import Path
SOLO = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")/'solo'
srcs = sorted(SOLO.rglob('*.py'))
print('sources() count =', len(srcs))
print('does it include chronicles.py?', any(p.name=='chronicles.py' for p in srcs))
print('first 3:', [p.name for p in srcs[:3]])
print('names sample:', [p.name for p in srcs if 'chron' in p.name])
print()
# the exact predicates from the test
for name in ('chronicles.py',):
    p = SOLO/name
    txt = p.read_text(encoding='utf-8', errors='replace')
    print(name, 'has sys.path.insert ->', 'sys.path.insert' in txt)
print()
print('=== what does the test actually scan? ===')
print('SOLO =', SOLO)
print('recursive rglob(*.py) picks up every subpackage too')
