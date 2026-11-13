"""r238: живой прогон подготовки книги с перезапуском процесса.

Что доказывается, а не описывается:

1. разбор идёт в процессе приложения, а не во вкладке: страницу никто не
   держит открытой, об этом скрипте приложение не знает;
2. на середине процесс получает SIGTERM (как при перезапуске `solo-app`),
   поднимается заново и продолжает прогон сам — в логе видно
   «поднял после перезапуска»;
3. за уже готовые блоки второй раз не платят: число вызовов модели после
   рестарта равно числу оставшихся блоков, а не всех.

Книга берётся из живой библиотеки только на чтение и копируется в отдельный
профиль (`--workdir`): живой профиль владельца не трогается, расходы идут в
свой `cloud-usage.sqlite3`, поэтому цену прогона видно честно.

    .venv/bin/python scripts/probe_r238_restart.py --pages 80 --restart-after 6
"""
import argparse,json,os,signal,sqlite3,subprocess,sys,time,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))

PORT=19216

def http(method,path,body=None,timeout=60):
    data=json.dumps(body).encode() if body is not None else None
    request=urllib.request.Request(f'http://127.0.0.1:{PORT}{path}',data=data,method=method,
        headers={'Content-Type':'application/json'} if data else {})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        return json.loads(response.read().decode('utf8') or '{}')

def start(workdir,log):
    environment=dict(os.environ,SOLO_PREPARATION_WATCHDOG='15')
    handle=open(log,'ab')
    process=subprocess.Popen([sys.executable,str(ROOT/'scripts/serve_r238.py'),
        '--data-dir',str(workdir),'--port',str(PORT)],stdout=handle,stderr=subprocess.STDOUT,
        cwd=str(ROOT),env=environment)
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        try:
            # `/api/health` отвечает без поля `status`: готовность видна по
            # `readiness`, а сам ответ — по `product`.
            health=http('GET','/api/health',timeout=2)
            if health.get('product') and health.get('llm',{}).get('cloud'):return process
        except Exception:pass
        if process.poll() is not None:raise SystemExit('Приложение не поднялось, лог: '+str(log))
        time.sleep(.3)
    raise SystemExit('Приложение не ответило на /health')

def stop(process,hard=False):
    if process.poll() is not None:return
    process.send_signal(signal.SIGKILL if hard else signal.SIGTERM)
    try:process.wait(timeout=60 if not hard else 10)
    except subprocess.TimeoutExpired:process.kill();process.wait()

def setup(live_path,profiles,workdir,document,limit_pages,name,description):
    """Отдельный профиль: копия страниц книги, свои базы и свой журнал расходов."""
    from solo.adventure_library import AdventureLibrary
    workdir.mkdir(parents=True,exist_ok=True)
    for leftover in ('solo.sqlite3','adventure.sqlite3','preparation.sqlite3','cloud-usage.sqlite3',
            'adventure-files','preparation-watchdog.log','app-1.log','app-2.log'):
        path=workdir/leftover
        if path.is_dir():
            import shutil;shutil.rmtree(path)
        elif path.exists():path.unlink()
    # Профиль приложения создаётся первым: `prepare_profile` требует пустой
    # каталог без чужого маркера, поэтому ключи копируются после него.
    from solo.prototype_profile import prepare_profile
    data=prepare_profile(workdir,offline=False)
    (workdir/'cloud-profiles.json').write_bytes(Path(profiles).read_bytes())
    secrets=Path(profiles).parent/'cloud-secrets'
    if secrets.is_dir():
        import shutil;shutil.copytree(secrets,workdir/'cloud-secrets',dirs_exist_ok=True)
    live=sqlite3.connect(Path(live_path).expanduser().as_uri()+'?mode=ro',uri=True)
    found=live.execute('SELECT id,format,bytes,status,pages,quality FROM documents WHERE id=? OR id LIKE ?',
        (document,document+'%')).fetchone()
    if found is None:raise SystemExit('Документ не найден в живой библиотеке: '+document)
    document=found[0]
    pages=[(number,label,content,objects) for number,label,content,objects in
        live.execute('SELECT number,label,content,objects FROM pages WHERE document=? ORDER BY number',(document,))]
    if limit_pages:pages=pages[:limit_pages]
    print('книга',name,'страниц',len(pages),'знаков',sum(len(content or '') for _,_,content,_ in pages),flush=True)
    # Библиотеку и черновик готовим тем же приложением, что поднимется процессом.
    from solo.app import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(data/'solo.sqlite3',None,local_port=PORT,experimental=True)) as client:
        # Библиотека того же профиля: порядок как в живом приложении — сначала
        # оно создаёт схемы, потом в них кладут скопированную книгу.
        library=AdventureLibrary(data)
        with library.db() as target:
            target.execute('INSERT OR REPLACE INTO documents(id,format,bytes,status,pages,quality) VALUES(?,?,?,?,?,?)',
                (document,found[1],found[2],found[3],len(pages),found[5]))
            target.executemany('INSERT OR REPLACE INTO pages(number,label,content,objects,document) VALUES(?,?,?,?,?)',
                [(number,label,content,objects,document) for number,label,content,objects in pages])
        draft=client.post('/api/creation/drafts').json();draft_id=draft['id'];revision=draft['revision']
        url='/api/creation/drafts/'+draft_id
        owned=client.app.state.creation.owned(draft_id);bundle=owned['body']['adventure_bundle']
        with library.db() as target:
            target.execute('INSERT OR REPLACE INTO bundle_files(bundle,document,original_name,purpose,parent) VALUES(?,?,?,?,?)',
                (bundle,document,name,'book',''))
            target.execute('INSERT OR REPLACE INTO jobs(id,bundle,document,original_name,purpose,status,done,total,message,created) VALUES(?,?,?,?,?,?,?,?,?,?)',
                ('r238-'+bundle[:8],bundle,document,name,'book','complete',len(pages),len(pages),'Страницы скопированы для прогона r238.',time.time()))
        with client.app.state.preparation.connection() as target:
            target.execute('INSERT OR REPLACE INTO creation_files(draft,document,active) VALUES(?,?,1)',(draft_id,document))
        client.put(url,json={'description':description,'revision':revision}).raise_for_status()
    print('черновик',draft_id,flush=True)
    return draft_id,len(pages),sum(len(content or '') for _,_,content,_ in pages)

def calls(workdir):
    path=workdir/'cloud-usage.sqlite3'
    if not path.is_file():return dict(total=0,ok=0,invalid=0,limit=0,spent=0.0)
    with sqlite3.connect(path) as db:
        row=db.execute("SELECT count(*),sum(status='ok'),sum(status='invalid_contract'),"
            "sum(error_code='stream_byte_limit'),coalesce(sum(cost),0)/1000000.0 FROM calls "
            "WHERE purpose='preparation_world'").fetchone()
    return dict(total=row[0] or 0,ok=row[1] or 0,invalid=row[2] or 0,limit=row[3] or 0,spent=round(row[4] or 0,4))

def run_state(workdir,run_id):
    with sqlite3.connect(workdir/'preparation.sqlite3') as db:
        db.row_factory=sqlite3.Row
        row=db.execute('SELECT status,done,total,worker,heartbeat,message FROM creation_runs WHERE id=?',(run_id,)).fetchone()
        if row is None:return None
        validated=db.execute("SELECT count(*) FROM creation_parts p JOIN creation_part_links l ON l.part=p.id "
            "WHERE l.run=? AND p.status='validated'",(run_id,)).fetchone()[0]
        return dict(status=row['status'],done=row['done'],total=row['total'],worker=row['worker'],
            heartbeat=row['heartbeat'],message=row['message'],validated=validated)

def validated_prefixes(workdir,run_id):
    """Отпечатки блоков, которые УЖЕ оплачены и приняты до перезапуска."""
    with sqlite3.connect(workdir/'preparation.sqlite3') as db:
        return [row[0][:16] for row in db.execute(
            'SELECT p.id FROM creation_parts p JOIN creation_part_links l ON l.part=p.id '
            "WHERE l.run=? AND p.status='validated'",(run_id,))]


def repayed_calls(workdir,run_id,prefixes,since):
    """Вызовы модели после перезапуска по уже готовым блокам — их быть не должно.

    `turn_ref` каждого вызова — ``<прогон>:<первые 16 знаков блока>``, поэтому
    «оплатили готовый блок второй раз» проверяется по журналу расходов, а не по
    обещаниям.
    """
    if not prefixes:return []
    path=workdir/'cloud-usage.sqlite3'
    if not path.is_file():return []
    with sqlite3.connect(path) as db:
        rows=[(part,count) for part,count in db.execute(
            'SELECT turn_ref,count(*) FROM calls WHERE purpose="preparation_world" AND created>=? GROUP BY turn_ref',(since,))]
    found=[]
    for part,count in rows:
        for prefix in prefixes:
            if (part or '').endswith(':'+prefix):found.append(dict(block=prefix,calls=count))
    return found


def wait_status(run_id,stop_at=None,final=('ready','partial','failed','blocked','cancelled'),timeout=3600):
    deadline=time.monotonic()+timeout;last=None
    while time.monotonic()<deadline:
        status=http('GET','/api/creation/runs/'+run_id)
        if status['status']!=last:
            print(time.strftime('%H:%M:%S'),status['status'],status['done'],'/',status['total'],flush=True);last=status['status']
        if stop_at is not None and status['done']>=stop_at and status['status']=='running':return status
        if status['status'] in final:return status
        time.sleep(2)
    raise SystemExit('Таймаут прогона')

def run(args):
    workdir=Path(args.workdir).resolve()
    draft_id,pages,characters=setup(args.live,args.profiles,workdir,args.document,args.pages,args.name,args.description)
    log1=workdir/'app-1.log';log2=workdir/'app-2.log'

    first=start(workdir,log1)
    started=http('POST','/api/creation/drafts/'+draft_id+'/prepare',
        dict(request_id=args.request,ruleset=args.ruleset,consent_to_send=True))
    if 'id' not in started:raise SystemExit('Подготовка не началась: '+json.dumps(started,ensure_ascii=False))
    run_id=started['id'];print('прогон',run_id,'план',started.get('total'),flush=True)
    before=wait_status(run_id,stop_at=args.restart_after)
    paid_before=calls(workdir);state_before=run_state(workdir,run_id)
    paid_blocks=validated_prefixes(workdir,run_id)
    restart_ts=time.time()
    print('перезапуск: готово',before['done'],'из',before['total'],'вызовов',paid_before['total'],flush=True)
    stop(first)

    print('процесс остановлен, состояние в базе:',run_state(workdir,run_id),flush=True)
    second=start(workdir,log2)
    after=wait_status(run_id)
    paid_after=calls(workdir);state_after=run_state(workdir,run_id)
    watchdog=(workdir/'preparation-watchdog.log').read_text(encoding='utf8') if (workdir/'preparation-watchdog.log').is_file() else ''
    first_log=log1.read_text(encoding='utf8') if log1.is_file() else ''
    second_log=log2.read_text(encoding='utf8') if log2.is_file() else ''
    stop(second)

    raised=[line for line in (first_log+second_log+watchdog).splitlines() if 'поднял после перезапуска' in line]
    repayed=repayed_calls(workdir,run_id,paid_blocks,restart_ts)
    remaining=before['total']-before['done']
    report=dict(document=args.document,name=args.name,pages=pages,characters=characters,run=run_id,
        status=after['status'],done=after['done'],total=after['total'],message=after['message'],
        before=dict(state=state_before,calls=paid_before),after=dict(state=state_after,calls=paid_after),
        calls_after_restart=paid_after['total']-paid_before['total'],blocks_remaining_at_restart=remaining,
        raised=raised,retry=after.get('retry'),
        paid_blocks_at_restart=len(paid_blocks),repayed_blocks=repayed,
        refuse_share=round(paid_after['invalid']/paid_after['total'],3) if paid_after['total'] else None,
        limit_share=round(paid_after['limit']/paid_after['total'],3) if paid_after['total'] else None,
        calls_per_block=round(paid_after['total']/max(1,after['done']),2),
        watchdog_log=watchdog.splitlines()[-8:])
    out=workdir/'report.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({key:report[key] for key in ('status','done','total','calls_after_restart',
        'blocks_remaining_at_restart','paid_blocks_at_restart','repayed_blocks','refuse_share',
        'limit_share','calls_per_block','raised')},ensure_ascii=False,indent=2))
    print('отчёт',out)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',default=str(ROOT/'runtime/web-local/adventure.sqlite3'))
    parser.add_argument('--profiles',default=str(ROOT/'runtime/web-local/cloud-profiles.json'))
    parser.add_argument('--workdir',default=str(ROOT/'reports/r238/live'))
    parser.add_argument('--document',default='fed0a540d18504088dfa17217e3f92ebebdab342f6f1de526bd8ddd8ec4202c3')
    parser.add_argument('--name',default='Out of the Abyss RUS.pdf')
    parser.add_argument('--pages',type=int,default=80)
    parser.add_argument('--restart-after',type=int,default=6,help='перезапустить, когда готово столько блоков')
    parser.add_argument('--ruleset',default='dnd2014')
    parser.add_argument('--description',default='Живой прогон r238: книга из библиотеки, перезапуск процесса на середине.')
    parser.add_argument('--request',default='r238-live')
    run(parser.parse_args())


if __name__=='__main__':main()
