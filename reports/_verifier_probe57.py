
import os
os.environ['SOLO_TEST']='1'
import json, sqlite3, glob
from pathlib import Path
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
# Across every real events table, how often does results[0] carry 'text' vs 'game_command'?
stats = {'text':0, 'game_command':0, 'neither':0, 'empty':0}
for db in glob.glob(str(ROOT/'**'/'*.sqlite3'), recursive=True):
    if '.venv' in db: continue
    try:
        c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        rows = c.execute('SELECT results FROM events').fetchall(); c.close()
    except Exception: continue
    for (raw,) in rows:
        try: r = json.loads(raw)
        except Exception: continue
        if not isinstance(r, list) or not r or not isinstance(r[0], dict):
            stats['empty'] += 1; continue
        f = r[0]
        if f.get('text'): stats['text'] += 1
        if f.get('game_command'): stats['game_command'] += 1
        if not f.get('text') and not f.get('game_command'): stats['neither'] += 1
print('results[0] across all real events in the tree:', stats)
print()
print('=> branch 1 (game.py:570) needs game_command AND narrative')
print('=> branch 2 (game.py:605) needs results[0].text — which no path writes')
