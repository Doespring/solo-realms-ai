
import sqlite3, sys, glob, json
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
con = sqlite3.connect(f'file:{ROOT/"adventure.sqlite3"}?mode=ro', uri=True)
print('tables in adventure.sqlite3:', [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")])
con.close()
# schema of events in any db that has it
for db in glob.glob(str(ROOT/'**'/'*.sqlite3'), recursive=True):
    if '.venv' in db: continue
    try:
        c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        row = c.execute("SELECT sql FROM sqlite_master WHERE name='events'").fetchone()
        c.close()
    except Exception: continue
    if row:
        print('\nschema in', Path(db).name, ':', row[0]); break

print()
print('=== nullability from storage.py:56 ===')
print('before_state TEXT NOT NULL,after_state TEXT NOT NULL')
print()
print('=== can json.dumps(None) reach the column? ; can the dict be non-flat? ===')
print('dump(None) =', json.dumps(None))
print('dump([1,2]) =', json.dumps([1,2]))
present = {}
for db in glob.glob(str(ROOT/'**'/'*.sqlite3'), recursive=True):
    if '.venv' in db: continue
    try:
        c = sqlite3.connect(f'file:{db}?mode=ro', uri=True); rows = c.execute('SELECT after_state FROM events').fetchall(); c.close()
    except Exception: continue
    for (raw,) in rows:
        try: v = json.loads(raw)
        except Exception: continue
        if isinstance(v, dict):
            for k in v: present[k] = present.get(k,0)+1
print('top-level keys ever seen in after_state:', sorted(present.items(), key=lambda x:-x[1])[:14])
