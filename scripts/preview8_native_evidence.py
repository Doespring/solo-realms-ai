"""Read-only state evidence from isolated Preview8 native profiles."""
import hashlib,json,pathlib,sqlite3,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/preview8'
profile,phase=sys.argv[1:3]
path=(OUT/profile/'solo.sqlite3').resolve()
assert path.is_relative_to(OUT.resolve())
with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
    rows=db.execute('SELECT id,name,version,state FROM campaigns').fetchall()
    integrity=db.execute('PRAGMA integrity_check').fetchall()
    foreign=db.execute('PRAGMA foreign_key_check').fetchall()
campaigns=[]
for cid,name,version,raw in rows:
    state=json.loads(raw)
    native=state.get('nat20')
    campaigns.append(dict(id=cid,name=name,version=version,native=native,
        native_sha256=hashlib.sha256(json.dumps(native,sort_keys=True).encode()).hexdigest() if native else None))
result=dict(profile=profile,phase=phase,campaigns=campaigns,integrity=integrity,foreign_keys=foreign,mock_ai=True,paid_calls=0)
(OUT/(profile+'-'+phase+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({**result,'campaigns':[{k:v for k,v in c.items() if k!='native'} for c in campaigns]},ensure_ascii=False))
