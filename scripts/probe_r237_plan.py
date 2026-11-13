"""r237: сколько кусков в плане разбора — без единого платного вызова.

План (`source_batches`) детерминирован: он зависит только от текста книги и
пределов подготовки. Поэтому «278 кусков» проверяются офлайн, на настоящей
книге из живой библиотеки, а не на модель — и повторяются после каждой правки.

    .venv/bin/python scripts/probe_r237_plan.py --document ffdf6324 --pages 109
    .venv/bin/python scripts/probe_r237_plan.py --document ffdf6324 --characters 429000

`--characters` режет страницы ровно по сумме знаков: план меряется на книге
того же объёма, что у владельца, без подгонки числа страниц.
"""
import argparse,json,sqlite3,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

from fastapi.testclient import TestClient

from solo.adventure_library import AdventureLibrary
from solo.app import create_app
from solo.preparation_limits import limits


def copy_document(live,document,limit_pages=None,characters=None):
    found=live.execute('SELECT id FROM documents WHERE id=? OR id LIKE ?',(document,document+'%')).fetchone()
    if found is None:raise SystemExit('Документ не найден в живой библиотеке: '+document)
    document=found[0]
    row=live.execute('SELECT format,bytes,status,pages,quality FROM documents WHERE id=?',(document,)).fetchone()
    pages=[(number,label,content,objects) for number,label,content,objects in
        live.execute('SELECT number,label,content,objects FROM pages WHERE document=? ORDER BY number',(document,))]
    if characters:
        kept=[];total=0
        for page in pages:
            text=page[2] or ''
            if total+len(text)>characters:
                continue
            kept.append(page);total+=len(text)
        pages=kept
    if limit_pages:pages=pages[:limit_pages]
    return 'f'*32+document[:32],row,pages


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',default=str(ROOT/'runtime/web-local/adventure.sqlite3'))
    parser.add_argument('--workdir',default=str(ROOT/'reports/r237/plan'))
    parser.add_argument('--document',default='ffdf6324')
    parser.add_argument('--pages',type=int,default=0)
    parser.add_argument('--characters',type=int,default=0)
    parser.add_argument('--json',default='')
    args=parser.parse_args()
    live=sqlite3.connect(Path(args.live).expanduser().as_uri()+'?mode=ro',uri=True)
    work=Path(args.workdir).expanduser();work.mkdir(parents=True,exist_ok=True)
    document,row,pages=copy_document(live,args.document,args.pages,args.characters)
    characters=sum(len(content or '') for _,_,content,_ in pages)
    print('document',document,'pages',len(pages),'chars',characters,flush=True)
    started=time.perf_counter()
    with TestClient(create_app(work/'solo.sqlite3',None)) as client:
        library=AdventureLibrary(work)
        with library.db() as target:
            target.execute('INSERT OR REPLACE INTO documents(id,format,bytes,status,pages,quality) VALUES(?,?,?,?,?,?)',
                (document,row[0],row[1],row[2],len(pages),row[4]))
            target.executemany('INSERT OR REPLACE INTO pages(number,label,content,objects,document) VALUES(?,?,?,?,?)',
                [(number,label,content,objects,document) for number,label,content,objects in pages])
        preparation=client.app.state.preparation_pipeline
        plan_started=time.perf_counter()
        plan=[batch for batch,_ in preparation.source_batches([document])]
        plan_seconds=round(time.perf_counter()-plan_started,2)
    elapsed=round(time.perf_counter()-started,2)
    sizes=[sum(len(p.get('text') or '') for p in batch) for batch in plan]
    empty=sum(1 for size in sizes if size==0)
    report=dict(document=document,characters=characters,pages=len(pages),blocks=len(plan),empty_blocks=empty,
        limits=limits(),min=min(sizes) if sizes else 0,max=max(sizes) if sizes else 0,
        median=sorted(sizes)[len(sizes)//2] if sizes else 0,plan_seconds=plan_seconds,seconds=elapsed,sizes=sizes)
    print(json.dumps({k:v for k,v in report.items() if k!='sizes'},ensure_ascii=False,indent=2))
    if args.json:Path(args.json).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('sizes',sizes)


if __name__=='__main__':main()
