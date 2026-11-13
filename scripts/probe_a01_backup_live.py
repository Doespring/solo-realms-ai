"""A01: живая проверка «Создать резервную копию» и «Экспорт данных» на базе с сиротой.

Копия делается через SQLite backup API, проверки идут по КОПИИ, находки попадают
в отчёт рядом с копией. Здесь проверяется весь путь по HTTP на отдельном порту,
как это делает кнопка на экране «Настройки → Данные и копии».

    .venv/bin/python scripts/probe_a01_backup_live.py
"""
import hashlib,json,sqlite3,subprocess,sys,time,urllib.request,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
WORK=ROOT/'runtime'/'a01-live'
PORT=19277
ORPHAN='нет-такой-кампании'

def md5(path):return hashlib.md5(Path(path).read_bytes()).hexdigest()

def call(method,path):
    request=urllib.request.Request('http://127.0.0.1:'+str(PORT)+path,method=method)
    try:
        with urllib.request.urlopen(request,timeout=60) as answer:
            return answer.status,answer.headers,answer.read()
    except urllib.error.HTTPError as error:
        return error.code,error.headers,error.read()

def build_profile():
    import shutil
    shutil.rmtree(WORK,ignore_errors=True);WORK.mkdir(parents=True)
    sys.path.insert(0,str(ROOT))
    from solo.contracts import Hero
    from solo.storage import Store
    store=Store(WORK/'solo.sqlite3')
    campaign=store.create('Кампания',Hero(name='Герой'))['id']
    with sqlite3.connect(store.path) as db:
        db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)',('chk-orphan',ORPHAN,'Сирота','{}',0,0.0,'[]'))
    return campaign

def copy_campaign(copy,campaign):
    with sqlite3.connect(copy) as db:
        return dict(row=db.execute('SELECT name FROM campaigns WHERE id=?',(campaign,)).fetchone(),
                    checkpoints=db.execute('SELECT count(*) FROM checkpoints').fetchone()[0])

def main():
    campaign=build_profile()
    log=(WORK/'server.log').open('w')
    server=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(ROOT/'scripts/serve_r238.py'),
                             '--data-dir',str(WORK),'--port',str(PORT)],stdout=log,stderr=subprocess.STDOUT)
    result={}
    try:
        for _ in range(120):
            if server.poll() is not None:raise SystemExit('сервер упал: см. '+str(WORK/'server.log'))
            try:
                if call('GET','/api/health')[0]==200:break
            except OSError:pass
            time.sleep(0.5)
        else:raise SystemExit('сервер не поднялся')
        before=md5(WORK/'solo.sqlite3')
        status,_,raw=call('POST','/api/data/backup')
        answer=json.loads(raw)
        folder=WORK/'backups'/answer['id']
        copy=folder/'solo.sqlite3'
        report=json.loads((folder/'integrity-report.json').read_text(encoding='utf-8'))
        orphan=report['files']['solo.sqlite3']['foreign_keys'][0]
        with sqlite3.connect(copy) as db:integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
        listed=json.loads(call('GET','/api/data/backups')[2])
        status_export,headers,archive=call('GET','/api/data/export')
        names=[];report_in_zip=None
        if status_export==200:
            (WORK/'export.zip').write_bytes(archive)
            with zipfile.ZipFile(WORK/'export.zip') as bundle:
                names=bundle.namelist();report_in_zip=json.loads(bundle.read('integrity-report.json').decode('utf-8'))['broken_links']
        after=md5(WORK/'solo.sqlite3')
        with sqlite3.connect(WORK/'solo.sqlite3') as db:
            kept=db.execute("SELECT campaign FROM checkpoints WHERE id='chk-orphan'").fetchone()
        result=dict(
            backup_status=status,copy_exists=copy.is_file(),copy_bytes=copy.stat().st_size if copy.is_file() else 0,
            broken_links=answer.get('broken_links'),summary=answer.get('summary'),report_file=answer.get('report_file'),
            orphan=dict(table=orphan['table'],rowid=orphan['rowid'],parent=orphan['parent'],columns=orphan['columns'],values=orphan['values']),
            copy_integrity=integrity,list_nonempty=[item['id'] for item in listed['backups']],
            list_broken=listed['backups'][0]['broken_links'] if listed['backups'] else None,
            export_status=status_export,export_bytes=len(archive),export_header=headers.get('X-Solo-Integrity'),
            export_names=names,export_report_broken=report_in_zip,
            md5_before=before,md5_after=after,md5_same=before==after,orphan_kept=kept,
            copy_campaign=copy_campaign(copy,campaign),
            live_campaign=campaign)
    finally:
        server.terminate()
        try:server.wait(timeout=20)
        except subprocess.TimeoutExpired:server.kill()
        log.close()
    (WORK/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
