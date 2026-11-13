"""r228: сколько стоит сборка мира из N секций и где она греется.

Живой прогон книги на 256 страниц дал 253 секции — и сборка (`compile`)
перестала заканчиваться за минуты. Скрипт берёт ГОТОВЫЕ ответы модели из
рабочего каталога прогона и меряет только сборку, без сети и без денег:

    .venv/bin/python scripts/probe_r228_compile.py --workdir reports/r228/live
"""
import argparse,cProfile,json,pstats,sqlite3,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

from solo.adventure_library import AdventureLibrary
from solo.prepared_world import PreparedWorldValidator
from solo.preparation_pipeline import active_mechanics


def batches(work):
    con=sqlite3.connect(work/'preparation.sqlite3');con.row_factory=sqlite3.Row
    run=con.execute('SELECT id FROM creation_runs ORDER BY created DESC LIMIT 1').fetchone()['id']
    documents={row[0] for row in con.execute('SELECT DISTINCT json_extract(source,\'$.pages[0].document\') FROM creation_parts p JOIN creation_part_links l ON l.part=p.id WHERE l.run=?',(run,))}
    bodies=[]
    for row in con.execute('SELECT p.body FROM creation_parts p JOIN creation_part_links l ON l.part=p.id WHERE l.run=? AND p.status="validated" ORDER BY l.ordinal',(run,)):
        if row['body']:bodies.append(json.loads(row['body']))
    return run,documents,bodies


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workdir',default=str(ROOT/'reports/r228/live'))
    parser.add_argument('--profile',action='store_true')
    parser.add_argument('--limit',type=int,default=0)
    args=parser.parse_args();work=Path(args.workdir)
    run,documents,bodies=batches(work)
    if args.limit:bodies=bodies[:args.limit]
    print('run',run,'documents',len(documents),'batches',len(bodies),'entities',sum(len(b.get('entities') or []) for b in bodies),flush=True)
    validator=PreparedWorldValidator(AdventureLibrary(work),sorted(documents))
    started=time.perf_counter()
    if args.profile:
        profiler=cProfile.Profile();profiler.enable()
        result=validator.compile(bodies,active_mechanics())
        profiler.disable()
        pstats.Stats(profiler).sort_stats('cumulative').print_stats(18)
    else:
        result=validator.compile(bodies,active_mechanics())
    print('compile seconds',round(time.perf_counter()-started,2),flush=True)
    print('ready',result['ready'],'locations',len((result['definition'].get('world') or {}).get('locations') or []),'quarantine',len(result.get('quarantine') or []))


if __name__=='__main__':main()
