
import os
os.environ['SOLO_TEST']='1'
import json, sys, sqlite3, glob
sys.path.insert(0, r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")

print('=== real events: what type is after_state after decode_event? ===')
seen = {}
for db in glob.glob(str(ROOT/'**'/'*.sqlite3'), recursive=True):
    if '.venv' in db: continue
    try:
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        rows = con.execute('SELECT after_state FROM events').fetchall()
        con.close()
    except Exception as e:
        continue
    if not rows: continue
    kinds = {}
    for (raw,) in rows:
        try:
            v = json.loads(raw)
        except Exception:
            kinds['UNPARSEABLE'] = kinds.get('UNPARSEABLE',0)+1; continue
        kinds[type(v).__name__] = kinds.get(type(v).__name__,0)+1
    seen[db] = (len(rows), kinds)
for db,(n,k) in seen.items():
    print(f'{Path(db).name:45} n={n:5} ' + str(k))
if not seen: print('no event rows found')

print()
print('=== is after_state nullable / writable as non-dict? ===')
con = sqlite3.connect(f'file:{ROOT/"adventure.sqlite3"}?mode=ro', uri=True)
print(con.execute("SELECT sql FROM sqlite_master WHERE name='events'").fetchone()[0])
con.close()

print()
print('=== dict() of a list / of None in real code: stored_command(event[after_state]) ===')
print('dict([1,2]) ->', end=' ')
try: print(dict([1,2]))
except Exception as e: print(type(e).__name__, e)
print('dict(None) ->', end=' ')
try: print(dict(None))
except Exception as e: print(type(e).__name__, e)
