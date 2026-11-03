
import os, re, subprocess
from pathlib import Path
os.environ['SOLO_TEST']='1'
ROOT = Path(r"C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI")
p = ROOT/'solo'/'game.py'
orig = p.read_bytes()
text = orig.decode('utf-8')
pat = re.compile(r"def _moved_on\(event, current\):(.*?)(?=\n\S)", re.S)
m = pat.search(text)
assert m, 'def not found'
print('--- OLD BODY ---'); print(m.group(0))
new_fn = ("def _moved_on(event, current):\n"
          "    import json as _json\n"
          "    try:\n"
          "        return _json.loads(event['after_state'] or '{}') != current.get('state')\n"
          "    except (ValueError, TypeError):\n"
          "        return False\n")
nl = '\r\n' if '\r\n' in text else '\n'
try:
    p.write_bytes(pat.sub(new_fn.replace('\n', nl), text, count=1).encode('utf-8'))
    env = dict(os.environ, SOLO_TEST='1', PYTHONPATH=str(ROOT))
    r = subprocess.run([str(ROOT/'.venv'/'Scripts'/'python.exe'), '-X','utf8','-B','-m','pytest',
        str(ROOT/'tests'/'test_replay_knows_the_table_moved.py'), '-q','-p','no:cacheprovider'],
        capture_output=True, text=True, env=env, cwd=str(ROOT))
    print('=== OLD _moved_on (reverted) ===')
    print((r.stdout or '')[-3000:])
    print('STDERR:', (r.stderr or '')[-1200:])
finally:
    p.write_bytes(orig)
print('restored:', p.read_bytes()==orig)
