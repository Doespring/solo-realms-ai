"""r237: живой прогон — сколько ждать входа и сколько ждать весь мир.

Что доказывается числами, а не словами:

1. план книги 429 тыс. знаков измеряется до оплаты и укладывается в десятки
   кусков, а не в 278 заготовок;
2. вход открывается по ПЕРВЫМ блокам: `playable` становится истинным, пока
   прогон ещё `running`, и в этот момент мир уже можно подтвердить и начать;
3. полный мир собирается в пределах пятнадцати минут.

Книга берётся из живой библиотеки только на чтение и копируется в отдельный
профиль: расходы идут в свой `cloud-usage.sqlite3`, живой профиль владельца
не трогается.

    .venv/bin/python scripts/probe_r237_live.py --characters 429000
    .venv/bin/python scripts/probe_r237_live.py --characters 429000 --confirm-world
"""
import argparse,json,shutil,sqlite3,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


def slice_pages(live,document,characters):
    found=live.execute('SELECT id,format,bytes,status,quality FROM documents WHERE id=? OR id LIKE ?',
        (document,document+'%')).fetchone()
    if found is None:raise SystemExit('Документ не найден в живой библиотеке: '+document)
    document=found[0];row=found[1:]
    pages=[(number,label,content,objects) for number,label,content,objects in
        live.execute('SELECT number,label,content,objects FROM pages WHERE document=? ORDER BY number',(document,))]
    if characters:
        kept=[];total=0
        for page in pages:
            text=page[2] or ''
            if total+len(text)>characters:break
            kept.append(page);total+=len(text)
        pages=kept
    return document,row,pages


def calls(workdir):
    path=workdir/'cloud-usage.sqlite3'
    if not path.is_file():return dict(total=0,ok=0,invalid=0,limit=0,spent=0.0)
    with sqlite3.connect(path) as db:
        row=db.execute("SELECT count(*),sum(status='ok'),sum(status='invalid_contract'),"
            "sum(error_code='stream_byte_limit'),coalesce(sum(cost),0)/1000000.0 FROM calls "
            "WHERE purpose='preparation_world'").fetchone()
    return dict(total=row[0] or 0,ok=row[1] or 0,invalid=row[2] or 0,limit=row[3] or 0,spent=round(row[4] or 0,4))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',default=str(ROOT/'runtime/web-local/adventure.sqlite3'))
    parser.add_argument('--profiles',default=str(ROOT/'runtime/web-local/cloud-profiles.json'))
    parser.add_argument('--workdir',default=str(ROOT/'reports/r237/live'))
    parser.add_argument('--document',default='ffdf6324')
    parser.add_argument('--name',default='Icewind Dale - Rime of the Frostmaiden RUS.pdf')
    parser.add_argument('--characters',type=int,default=429000)
    parser.add_argument('--ruleset',default='dnd2014')
    parser.add_argument('--description',default='Живой прогон r237: книга из библиотеки, старт с главы 1.')
    parser.add_argument('--request',default='r237-live')
    parser.add_argument('--timeout',type=float,default=1800)
    parser.add_argument('--poll',type=float,default=1.0)
    parser.add_argument('--confirm-world',action='store_true',
        help='подтвердить вход в момент «можно играть» — проверка, что кнопка не заперта')
    parser.add_argument('--json',default='')
    args=parser.parse_args()

    workdir=Path(args.workdir).resolve();workdir.mkdir(parents=True,exist_ok=True)
    for leftover in ('solo.sqlite3','adventure.sqlite3','preparation.sqlite3','cloud-usage.sqlite3',
            'adventure-files','preparation-watchdog.log','app.log'):
        path=workdir/leftover
        if path.is_dir():shutil.rmtree(path)
        elif path.exists():path.unlink()
    from solo.prototype_profile import prepare_profile
    data=prepare_profile(workdir,offline=False)
    (workdir/'cloud-profiles.json').write_bytes(Path(args.profiles).expanduser().read_bytes())
    secrets=Path(args.profiles).expanduser().parent/'cloud-secrets'
    if secrets.is_dir():shutil.copytree(secrets,workdir/'cloud-secrets',dirs_exist_ok=True)

    live=sqlite3.connect(Path(args.live).expanduser().as_uri()+'?mode=ro',uri=True)
    document,row,pages=slice_pages(live,args.document,args.characters)
    characters=sum(len(content or '') for _,_,content,_ in pages)
    print('книга',args.name,'страниц',len(pages),'знаков',characters,flush=True)

    from fastapi.testclient import TestClient
    from solo.app import create_app
    from solo.adventure_library import AdventureLibrary
    with TestClient(create_app(data/'solo.sqlite3',None,experimental=True)) as client:
        library=AdventureLibrary(data)
        with library.db() as target:
            target.execute('INSERT OR REPLACE INTO documents(id,format,bytes,status,pages,quality) VALUES(?,?,?,?,?,?)',
                (document,row[0],row[1],row[2],len(pages),row[3]))
            target.executemany('INSERT OR REPLACE INTO pages(number,label,content,objects,document) VALUES(?,?,?,?,?)',
                [(number,label,content,objects,document) for number,label,content,objects in pages])
        draft=client.post('/api/creation/drafts').json();draft_id=draft['id'];revision=draft['revision']
        url='/api/creation/drafts/'+draft_id
        owned=client.app.state.creation.owned(draft_id);bundle=owned['body']['adventure_bundle']
        with library.db() as target:
            target.execute('INSERT OR REPLACE INTO bundle_files(bundle,document,original_name,purpose,parent) VALUES(?,?,?,?,?)',
                (bundle,document,args.name,'book',''))
            target.execute('INSERT OR REPLACE INTO jobs(id,bundle,document,original_name,purpose,status,done,total,message,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('r237-'+bundle[:8],bundle,document,args.name,'book','complete',len(pages),len(pages),'Страницы скопированы для прогона r237.',time.time()))
        with client.app.state.preparation.connection() as target:
            target.execute('INSERT OR REPLACE INTO creation_files(draft,document,active) VALUES(?,?,1)',(draft_id,document))
        client.put(url,json={'description':args.description,'revision':revision}).raise_for_status()
        started_at=time.time()
        response=client.post(url+'/prepare',json=dict(request_id=args.request,ruleset=args.ruleset,
            consent_to_send=True,world_structure=True))
        if response.status_code!=200:raise SystemExit('Подготовка не началась: '+response.text[:600])
        run=response.json();run_id=run['id']
        print('прогон',run_id,'план',run.get('total'),flush=True)
        playable_at=None;playable_view=None;confirmed=None;last=None
        deadline=time.monotonic()+args.timeout
        while True:
            status=client.get('/api/creation/runs/'+run_id).json()
            done,total=status['done'],status['total']
            if (status['status'],done,total)!=(last or (None,None,None)):
                print(str(time.strftime('%H:%M:%S')),status['status'],done,'/',total,
                    'playable' if status.get('playable') else '',flush=True)
                last=(status['status'],done,total)
            if status.get('playable') and playable_at is None:
                playable_at=time.time()-started_at;playable_view=status
                # Вход открыт: полный мир уже собран по главе 1, кампания играбельна.
                readiness=client.get(url+'/world-readiness')
                print('можно играть через',round(playable_at,1),'с; world-readiness',
                    readiness.status_code,flush=True)
                if args.confirm_world and readiness.status_code==200:
                    # Мир мог ещё не иметь run_id в проекции (вход открыт, а
                    # проекция пересобирается): подтверждаем по известному id.
                    view=readiness.json()
                    print('readiness at playable:',json.dumps(view,ensure_ascii=False)[:400],flush=True)
                    body=dict(expected_revision=view['revision'],
                        run_id=view.get('run_id') or run['id'],confirm=True)
                    answer=client.post(url+'/confirm-world',json=body)
                    confirmed=dict(status=answer.status_code,
                        world_ready=answer.json().get('world_readiness',{}).get('world_ready') if answer.status_code==200 else answer.text[:200])
                    print('подтверждение мира во время прогона:',confirmed,flush=True)
            if status['status'] not in ('queued','running'):break
            if time.monotonic()>deadline:raise SystemExit('Таймаут прогона')
            time.sleep(args.poll)
        full_at=time.time()-started_at
        final=status
        readiness=client.get(url+'/world-readiness')
        draft_after=client.get(url).json()
        report=dict(document=document,name=args.name,pages=len(pages),characters=characters,run=run_id,
            plan_blocks=run.get('total'),status=final['status'],done=final['done'],total=final['total'],
            message=final['message'],playable_at_s=round(playable_at,1) if playable_at else None,
            playable_done=playable_view['done'] if playable_view else None,
            playable_total=playable_view['total'] if playable_view else None,
            playable_blocks=(playable_view.get('playable') or {}).get('blocks') if playable_view else None,
            full_at_s=round(full_at,1),calls=calls(workdir),
            calls_per_block=round(calls(workdir)['total']/max(1,final['done']),2),
            split_sections=(final.get('structure') or {}).get('split_sections'),
            limit_hits=(final.get('structure') or {}).get('limit_hits'),
            refused_share=round((calls(workdir)['invalid']/calls(workdir)['total']),3) if calls(workdir)['total'] else None,
            world_readiness=dict(status=readiness.status_code,
                ready=readiness.json().get('ready') if readiness.status_code==200 else None,
                world_ready=readiness.json().get('world_ready') if readiness.status_code==200 else None,
                reasons=readiness.json().get('reasons') if readiness.status_code==200 else None),
            confirmed_during_run=confirmed,draft_prepared=bool((draft_after.get('preparation') or {}).get('playable')),
            errors=final.get('errors'))
        path=Path(args.json) if args.json else workdir/'report.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({key:report[key] for key in ('plan_blocks','status','done','total','playable_at_s',
            'playable_done','playable_total','full_at_s','calls_per_block','refused_share','split_sections',
            'world_readiness','confirmed_during_run')},ensure_ascii=False,indent=2))
        print('отчёт',path)


if __name__=='__main__':main()
