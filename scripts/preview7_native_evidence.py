"""Read-only evidence for the actual extracted Windows preview's test profile."""
import hashlib, json, pathlib, sqlite3, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/preview7'
with sqlite3.connect((OUT / 'native-final-profile/solo.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
    campaign_id, version, raw = db.execute('SELECT id,version,state FROM campaigns').fetchone()
    integrity = db.execute('PRAGMA integrity_check').fetchall()
    foreign_keys = db.execute('PRAGMA foreign_key_check').fetchall()
state = json.loads(raw)
native = state['nat20']
result = dict(phase=sys.argv[1], campaign_id=campaign_id, version=version,
              native=native, integrity=integrity, foreign_keys=foreign_keys,
              native_sha256=hashlib.sha256(json.dumps(native, sort_keys=True).encode()).hexdigest(),
              mock_ai=True, paid_calls=0)
if sys.argv[1] != 'saved':
    before = json.loads((OUT / 'native-saved.json').read_text(encoding='utf-8'))
    result['saved_native_equal'] = before['native'] == native
(OUT / f'native-{sys.argv[1]}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k != 'native'}, ensure_ascii=False))
