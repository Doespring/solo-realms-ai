"""r228: живой прогон подготовки книги на настоящем тексте библиотеки.

Зачем: потолок 96 КБ считается из max_tokens роли, и «прошло без
stream_byte_limit» доказывается только настоящей моделью на настоящей книге.
Скрипт не трогает живую библиотеку: страницы выбранного документа копируются
в отдельный временный профиль (adventure.sqlite3 открывается только на чтение),
а расходы и черновик остаются в этом же временном профиле.

    .venv/bin/python scripts/probe_r228_book.py --document af2408c9 --pages 2
    .venv/bin/python scripts/probe_r228_book.py --document af2408c9

--pages ограничивает текст первыми страницами: так дешёвый смоук меряет цену
и время одного вызова до полного прогона.
"""
import argparse,json,shutil,sqlite3,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.cloud import CloudLLM
from solo.cloud_cost import CostLedger
from solo.cloud_settings import CloudSettings
from solo.adventure_library import AdventureLibrary


def copy_document(live,document,limit_pages=None,from_page=None):
    """Страницы живого документа — только чтение, копия под новым id."""
    found=live.execute('SELECT id FROM documents WHERE id=? OR id LIKE ?',(document,document+'%')).fetchone()
    if found is None:raise SystemExit('Документ не найден в живой библиотеке: '+document)
    document=found[0]
    row=live.execute('SELECT format,bytes,status,pages,quality FROM documents WHERE id=?',(document,)).fetchone()
    pages=[(number,label,content,objects) for number,label,content,objects in
        live.execute('SELECT number,label,content,objects FROM pages WHERE document=? ORDER BY number',(document,))]
    if from_page:pages=[page for page in pages if page[0]>=from_page]
    if limit_pages:pages=pages[:limit_pages]
    return 'f'*32+document[:32],row,pages


def run(args):
    live=sqlite3.connect(Path(args.live).expanduser().as_uri()+'?mode=ro',uri=True)
    work=Path(args.workdir).expanduser();work.mkdir(parents=True,exist_ok=True)
    for leftover in (() if args.keep else ('solo.sqlite3','adventure.sqlite3','preparation.sqlite3','cloud-usage.sqlite3','adventure-files')):
        path=work/leftover
        if path.is_dir():shutil.rmtree(path)
        elif path.exists():path.unlink()
    settings=CloudSettings(Path(args.profiles).expanduser())
    ledger=CostLedger(work/'cloud-usage.sqlite3')
    llm=CloudLLM(settings,ledger)
    document,row,pages=copy_document(live,args.document,args.pages,args.from_page)
    print('document',document,'pages',len(pages),'chars',sum(len(content or '') for _,_,content,_ in pages),flush=True)
    with TestClient(create_app(work/'solo.sqlite3',llm)) as client:
        library=AdventureLibrary(work)  # тот же каталог профиля, что у приложения
        with library.db() as target:
            target.execute('INSERT OR REPLACE INTO documents(id,format,bytes,status,pages,quality) VALUES(?,?,?,?,?,?)',
                (document,row[0],row[1],row[2],len(pages),row[4]))
            target.executemany('INSERT OR REPLACE INTO pages(number,label,content,objects,document) VALUES(?,?,?,?,?)',
                [(number,label,content,objects,document) for number,label,content,objects in pages])
        if args.keep:
            # Продолжение: ТОТ ЖЕ черновик. Иначе identity меняется, сигнатуры
            # частей не совпадают и книга оплачивается заново целиком.
            with client.app.state.preparation.connection() as db:
                row=db.execute('SELECT r.draft,COUNT(*) AS done FROM creation_runs r'
                    ' JOIN creation_part_links l ON l.run=r.id JOIN creation_parts p ON p.id=l.part'
                    " WHERE p.status='validated' GROUP BY r.draft ORDER BY done DESC LIMIT 1").fetchone()
            if row is None:raise SystemExit('Нечего продолжать: нет частично подготовленного черновика')
            draft_id=row['draft'];revision=client.app.state.creation.get(draft_id)['revision']
            url='/api/creation/drafts/'+draft_id
            print('continue draft',draft_id,'validated blocks',row['done'],flush=True)
        else:
            draft=client.post('/api/creation/drafts').json();draft_id=draft['id'];revision=draft['revision']
            url='/api/creation/drafts/'+draft_id
        # Книга должна быть привязана к черновику: без bundle_files и creation_files
        # подготовка уходит в режим «только описание» и книгу не читает вовсе.
        owned=client.app.state.creation.owned(draft_id);bundle=owned['body']['adventure_bundle']
        with library.db() as target:
            target.execute('INSERT OR REPLACE INTO bundle_files(bundle,document,original_name,purpose,parent) VALUES(?,?,?,?,?)',
                (bundle,document,args.name,'book',''))
            target.execute('INSERT OR REPLACE INTO jobs(id,bundle,document,original_name,purpose,status,done,total,message,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('r228-'+bundle[:8],bundle,document,args.name,'book','complete',len(pages),len(pages),'Страницы скопированы для прогона r228.',time.time()))
        with client.app.state.preparation.connection() as target:
            target.execute('INSERT OR REPLACE INTO creation_files(draft,document,active) VALUES(?,?,1)',(draft_id,document))
        client.put(url,json={'description':'Живой прогон r228 по книге из библиотеки','revision':revision}).raise_for_status()
        response=client.post(url+'/prepare',json=dict(request_id=args.request,ruleset=args.ruleset,consent_to_send=True))
        started=response.json();print('start',response.status_code,started.get('id'),flush=True)
        if response.status_code!=200:raise SystemExit(str(started))
        deadline=time.monotonic()+args.timeout;last=None
        while True:
            status=client.get('/api/creation/runs/'+started['id']).json()
            if status['status'] not in ('queued','running'):break
            if status.get('message')!=last:print(time.strftime('%H:%M:%S'),status['status'],status['done'],'/',status['total'],status['message'],flush=True);last=status.get('message')
            if time.monotonic()>deadline:raise SystemExit('Таймаут прогона')
            time.sleep(args.poll)
        with sqlite3.connect(work/'cloud-usage.sqlite3') as cost_db:
            cost=[(row[0],round(row[1]/1000000,4)) for row in cost_db.execute(
                "SELECT currency,COALESCE(SUM(CASE WHEN status='reserved' THEN reserved ELSE cost END),0) FROM calls GROUP BY currency")]
        summary=ledger.summary()
        report=dict(status=status['status'],ready=status['ready'],done=status['done'],total=status['total'],
            message=status['message'],structure=status['structure'],errors=status['errors'],
            cost=dict(spent=cost,calls=len(summary.get('calls') or [])),
            sections=[dict(section=r.get('section'),bytes=r.get('bytes'),limit=r.get('byte_limit'),
                headroom=r.get('headroom'),calls=r.get('calls'),depth=r.get('split_depth'),limit_code=r.get('limit_code'))
                for r in status['structure']['responses']])
        print(json.dumps(report,ensure_ascii=False,indent=2))
        out=work/'r228-report.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print('report',out)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',default=str(ROOT/'runtime/web-local/adventure.sqlite3'))
    parser.add_argument('--profiles',default=str(ROOT/'runtime/web-local/cloud-profiles.json'))
    parser.add_argument('--workdir',default=str(ROOT/'reports/r228/live'))
    parser.add_argument('--document',default='af2408c9')
    parser.add_argument('--name',default='The Wild Beyond the Witchlight RUS.pdf')
    parser.add_argument('--pages',type=int,default=0)
    parser.add_argument('--from-page',type=int,default=0)
    parser.add_argument('--ruleset',default='dnd2014')
    parser.add_argument('--timeout',type=float,default=3600)
    parser.add_argument('--poll',type=float,default=3)
    parser.add_argument('--keep',action='store_true',help='не чистить рабочий каталог: догоняет незавершённые блоки')
    parser.add_argument('--request',default='r228-live')
    run(parser.parse_args())


if __name__=='__main__':main()
