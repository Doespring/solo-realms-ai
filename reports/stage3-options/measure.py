"""Unprofiled matched options timings; separate import, no server or provider."""
import os,sys,time,json,hashlib
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1]
sys.path.insert(0,str(ROOT));os.chdir(ROOT)
label=sys.argv[1];profile=(OUT/sys.argv[2]).resolve()
os.environ.update(SOLO_TEST='1',PYTHONDONTWRITEBYTECODE='1',SOLO_DATA_DIR=str(profile))
start=time.perf_counter()
from solo.prototype_ui import creation_options
from solo import content_verification_cache as cache
imports=(time.perf_counter()-start)*1000
count=[0];original=cache._sha_file
def counted(path):count[0]+=1;return original(path)
cache._sha_file=counted
rows=[]
for index in range(2):
    if hasattr(creation_options,'cache_clear'):creation_options.cache_clear()
    before=count[0];start=time.perf_counter();value=creation_options();elapsed=(time.perf_counter()-start)*1000
    rows.append(dict(options_ms=round(elapsed,3),sha_files=count[0]-before))
encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
report=dict(label=label,profile=str(profile),import_ms=round(imports,3),runs=rows,
    dto_sha256=hashlib.sha256(encoded).hexdigest(),bytes=len(encoded),paid_calls=0,profiler=False,
    conditions='Same OS session; filesystem cache not flushed, imports excluded; new process for persisted measurement')
(OUT/(label+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
if label=='before-fresh':(OUT/'before-options.json').write_bytes(encoded)
print(json.dumps(report))
