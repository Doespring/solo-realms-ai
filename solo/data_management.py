"""Local profile copies and bounded restore planning. Restoration happens with the backend stopped."""
import json,sqlite3,uuid,zipfile,shutil,os,re
from pathlib import Path
from contextlib import closing
from .user_data import backup,check_db,snapshot,MANAGED

def candidate(data,id):
    if not re.fullmatch(r'[A-Za-z0-9._ -]{1,140}',id):raise ValueError('Некорректный идентификатор копии')
    parent=(Path(data)/'backups').resolve();path=parent/id
    if path.is_symlink() or not path.resolve().is_relative_to(parent) or (path/'solo.sqlite3').is_symlink() or not (path/'solo.sqlite3').is_file():raise ValueError('Копия не найдена')
    return path
def preview(data,id):
    path=candidate(data,id);check_db(path/'solo.sqlite3',campaign=True)
    with closing(sqlite3.connect((path/'solo.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:return dict(id=id,campaigns=c.execute('SELECT count(*) FROM campaigns').fetchone()[0],schema=c.execute('PRAGMA user_version').fetchone()[0],integrity='ok')
def plan_restore(data,id):
    preview(data,id);safety=backup(data,'before-restore');Path(data,'pending-restore.json').write_text(json.dumps(dict(backup=id,safety=safety.name)),encoding='utf-8');return dict(status='restart_required',safety=safety.name)
def consume_restore(data):
    data=Path(data);plan=data/'pending-restore.json'
    if not plan.exists():return False
    body=json.loads(plan.read_text(encoding='utf-8'));source=candidate(data,body['backup']);preview(data,body['backup'])
    staged=data/('.restore-'+uuid.uuid4().hex);files=snapshot(source,staged);rollback=data/('.rollback-'+uuid.uuid4().hex);rollback.mkdir();moved=[];new=[]
    try:
        for name in MANAGED:
            for suffix in ('','-wal','-shm') if name.endswith('.sqlite3') else ('',):
                old=data/(name+suffix)
                if old.exists():old.rename(rollback/old.name);moved.append(old.name)
        for name in files:(staged/name).rename(data/name);new.append(name)
        check_db(data/'solo.sqlite3',campaign=True)
    except BaseException:
        for name in reversed(new):(data/name).rename(staged/name)
        for name in reversed(moved):(rollback/name).rename(data/name)
        raise
    # Keep rollback material; never recursively remove player data or protected keys.
    plan.rename(data/('restored-'+uuid.uuid4().hex+'.json'));return True
def register_data_api(app,store,game):
    from fastapi.responses import FileResponse
    data=store.path.parent
    @app.get('/api/data/backups')
    def backups():
        entries=[];folder=data/'backups'
        for path in sorted(folder.iterdir(),reverse=True) if folder.exists() else []:
            if path.is_dir() and not path.is_symlink() and (path/'solo.sqlite3').is_file():entries.append(dict(id=path.name,name=path.name,created=__import__('datetime').datetime.fromtimestamp(path.stat().st_mtime,__import__('datetime').timezone.utc).isoformat(timespec='seconds')))
        with store.connection() as c:schema=c.execute('PRAGMA user_version').fetchone()[0]
        return dict(profile=str(data),schema=schema,backups=entries[:100])
    @app.get('/api/data/backups/{id}/preview')
    def check_backup(id:str):return preview(data,id)
    @app.post('/api/data/backup')
    async def copy_profile():
        async with game.operation:return dict(id=backup(data).name)
    @app.get('/api/data/export')
    async def export():
        async with game.operation:
            folder=backup(data,'export');archive=folder/'SoloRealmsAI-data.zip'
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
                for name in ('solo.sqlite3','cloud-usage.sqlite3','cloud-profiles.json','desktop.json','preparation.sqlite3','adventure.sqlite3','snapshot.json'):
                    if (folder/name).is_file():z.write(folder/name,name)
                for file in (folder/'adventure-files').glob('*'):
                    if file.is_file():z.write(file,'adventure-files/'+file.name)
            return FileResponse(archive,filename='SoloRealmsAI-data.zip',media_type='application/zip')
