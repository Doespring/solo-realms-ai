"""Local profile copies and bounded restore planning. Restoration happens with the backend stopped."""
import json,sqlite3,uuid,zipfile,shutil,os,re,time
from datetime import datetime,timezone
from pathlib import Path
from contextlib import closing
from .user_data import backup,check_db,integrity_report,snapshot,MANAGED,REPORT_NAME

#: A profile carries a library copy per campaign and a picture per page, so the
#: size of a download is counted before the copy is written, not after it has
#: filled the disk. A refused download changes nothing on the profile.
MAX_EXPORT_BYTES=1024*1024*1024
#: Copies a person asked for are kept; once the count passes this the oldest
#: ones go. A copy the planned restore points at is never dropped.
MAX_BACKUPS=5
#: A copy a killed process left behind is not a backup: it is swept by age.
EXPORT_STALE_SECONDS=3600
EXPORT_NAME='SoloRealmsAI-data.zip'
EXPORT_FILES=('solo.sqlite3','cloud-usage.sqlite3','cloud-profiles.json','desktop.json','preparation.sqlite3','adventure.sqlite3','snapshot.json',REPORT_NAME)
class ExportRefused(ValueError):
    """Отказ скачивания с кодом: экран обязан назвать причину словами, а не молчать."""
    def __init__(self,message,code='export_refused'):super().__init__(message);self.code=code

def candidate(data,id):
    if not re.fullmatch(r'[A-Za-z0-9._ -]{1,140}',id):raise ValueError('Некорректный идентификатор копии')
    parent=(Path(data)/'backups').resolve();path=parent/id
    if path.is_symlink() or not path.resolve().is_relative_to(parent) or (path/'solo.sqlite3').is_symlink() or not (path/'solo.sqlite3').is_file():raise ValueError('Копия не найдена')
    return path
def preview(data,id):
    """Проверка копии: повреждённая копия — отказ, нарушенные связи — предупреждение.

    Копия, снятая с базы с нарушенными связями, остаётся восстановимой: восстановление
    вернёт записи как есть и ничего не удалит, поэтому поводом для отказа это не служит.
    """
    path=candidate(data,id);health=check_db(path/'solo.sqlite3',campaign=True)
    with closing(sqlite3.connect((path/'solo.sqlite3').as_uri()+'?mode=ro',uri=True)) as c:campaigns=c.execute('SELECT count(*) FROM campaigns').fetchone()[0]
    broken=len(health['foreign_keys'])
    return dict(id=id,campaigns=campaigns,schema=health['schema'],integrity=health['integrity'],broken_links=health['foreign_keys'],
                warning=('Связи нарушены у '+str(broken)+' записей: восстановление вернёт их как есть, ничего не удаляя') if broken else '')
def _write_plan(plan,body):
    """План пишется через замену: оборванная запись не оставит половину JSON."""
    temporary=plan.with_name(plan.name+'.tmp')
    temporary.write_text(json.dumps(body),encoding='utf-8');temporary.replace(plan)
def plan_restore(data,id):
    """План восстановления; выбранная копия закреплена ДО уборки старых.

    pending-restore.json пишется до уборки: иначе при заполненной квоте новая
    страховочная копия вытесняет выбранную, и следующее открытие приложения
    падает на «Копия не найдена» — игроку не выбраться без правки файлов.
    """
    checked=preview(data,id);plan=Path(data)/'pending-restore.json'
    _write_plan(plan,dict(backup=id,safety=None))
    try:safety=backup(data,'before-restore')
    except BaseException:
        plan.unlink(missing_ok=True);raise
    _write_plan(plan,dict(backup=id,safety=safety.name))
    prune_backups(data,pinned={id,safety.name})
    return dict(status='restart_required',safety=safety.name,warning=checked['warning'])
def consume_restore(data):
    data=Path(data);plan=data/'pending-restore.json'
    if not plan.exists():return False
    body=json.loads(plan.read_text(encoding='utf-8'));source=candidate(data,body['backup']);preview(data,body['backup'])
    staged=data/('.restore-'+uuid.uuid4().hex)
    try:files=snapshot(source,staged)
    except BaseException:
        shutil.rmtree(staged,ignore_errors=True);raise
    rollback=data/('.rollback-'+uuid.uuid4().hex);rollback.mkdir();moved=[];new=[]
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
    # Сценарий восстановления отработал: его пустой каркас в профиле не нужен.
    shutil.rmtree(staged,ignore_errors=True)
    # Keep rollback material; never recursively remove player data or protected keys.
    plan.rename(data/('restored-'+uuid.uuid4().hex+'.json'));return True

def profile_bytes(data):
    """How much a download would carry, counted before the copy is written."""
    data=Path(data);total=0
    for name in MANAGED:
        source=data/name
        if not source.exists() or source.is_symlink():continue
        if source.is_file():total+=source.stat().st_size
        elif source.is_dir():
            for item in source.rglob('*'):
                try:
                    if item.is_file() and not item.is_symlink():total+=item.stat().st_size
                except OSError:continue
    return total

def prune_backups(data,keep=MAX_BACKUPS,pinned=None):
    """Oldest library copies go first; the copy a planned restore points at stays.

    Only folders under `backups` that hold a campaign database are copies — the
    live profile, the books and the protected keys are never touched here.
    """
    folder=Path(data)/'backups';removed=[]
    if not folder.is_dir():return removed
    pinned=set(pinned or ())
    plan=Path(data)/'pending-restore.json'
    if plan.is_file():
        try:
            body=json.loads(plan.read_text(encoding='utf-8'));pinned|={str(body.get('backup') or ''),str(body.get('safety') or '')}
        except (OSError,ValueError):pass
    copies=[path for path in folder.iterdir() if path.is_dir() and not path.is_symlink() and (path/'solo.sqlite3').is_file()]
    copies.sort(key=lambda path:(path.stat().st_mtime_ns,path.name),reverse=True)
    for path in copies[max(1,int(keep)):]:
        if path.name in pinned:continue
        shutil.rmtree(path,ignore_errors=True);removed.append(path.name)
    return removed

def sweep_exports(data,now=None):
    """Copies a crashed or killed download left behind are removed by age."""
    root=Path(data)/'.exports';removed=[]
    if not root.is_dir():return removed
    now=time.time() if now is None else now
    for path in list(root.iterdir()):
        try:
            if not path.is_dir() or path.is_symlink() or now-path.stat().st_mtime<EXPORT_STALE_SECONDS:continue
            shutil.rmtree(path,ignore_errors=True);removed.append(path.name)
        except OSError:continue
    return removed

def prepare_export(data):
    """The temporary copy a download is zipped from: not a backup, gone with the answer.

    An export is not «сделай резервную копию»: it lives under `.exports`, so it
    never shows up in the copy list and never counts against the quota.
    """
    data=Path(data).resolve();sweep_exports(data)
    size=profile_bytes(data)
    if size>MAX_EXPORT_BYTES:raise ExportRefused('Данных '+str(size//(1024*1024))+' МБ — больше лимита скачивания '+str(MAX_EXPORT_BYTES//(1024*1024))+' МБ; профиль не изменён','export_too_large')
    free=shutil.disk_usage(data).free
    if free<size*2+64*1024*1024:raise ExportRefused('Недостаточно места для подготовки скачивания: данных '+str(size//(1024*1024))+' МБ, свободно '+str(free//(1024*1024))+' МБ','export_no_space')
    folder=data/'.exports'/('export-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    # allow_damaged: скачивание — единственный путь, где повреждённая база всё равно
    # уезжает к игроку сырым снимком с отчётом; молчание данные не вернёт.
    try:
        snapshot(data,folder,allow_damaged=True)
        # Причина, если она есть, едет в том же архиве: отчёт рядом с копией внутри ZIP.
        integrity_report(folder)
    except BaseException:
        finish_export(folder);raise
    return folder

def write_export(folder):
    """The ZIP is built from the temporary copy, so the live profile is not read while it is answered."""
    folder=Path(folder);archive=folder/EXPORT_NAME
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for name in EXPORT_FILES:
            if (folder/name).is_file():z.write(folder/name,name)
        for file in (folder/'adventure-files').glob('*'):
            if file.is_file():z.write(file,'adventure-files/'+file.name)
    return archive

def finish_export(folder):
    """Runs after the archive has been sent: the download leaves nothing behind."""
    folder=Path(folder);shutil.rmtree(folder,ignore_errors=True)
    try:folder.parent.rmdir()
    except OSError:pass

def register_data_api(app,store,game):
    from fastapi.responses import FileResponse,JSONResponse
    from starlette.background import BackgroundTask
    data=store.path.parent
    @app.get('/api/data/backups')
    def backups():
        entries=[];folder=data/'backups'
        for path in sorted(folder.iterdir(),reverse=True) if folder.exists() else []:
            if path.is_dir() and not path.is_symlink() and (path/'solo.sqlite3').is_file():
                broken=0;damaged=False;report=path/REPORT_NAME
                if report.is_file():
                    try:
                        body=json.loads(report.read_text(encoding='utf-8'))
                        broken=int(body.get('broken_links') or 0);damaged=bool(body.get('damaged'))
                    except (OSError,ValueError):broken=0
                entries.append(dict(id=path.name,name=path.name,created=__import__('datetime').datetime.fromtimestamp(path.stat().st_mtime,__import__('datetime').timezone.utc).isoformat(timespec='seconds'),broken_links=broken,damaged=damaged))
        with store.connection() as c:schema=c.execute('PRAGMA user_version').fetchone()[0]
        return dict(profile=str(data),schema=schema,backups=entries[:100])
    @app.get('/api/data/backups/{id}/preview')
    def check_backup(id:str):return preview(data,id)
    @app.post('/api/data/backup')
    async def copy_profile():
        # Отказ копии тоже называет причину и код: нарушенная связь копию не отменяет,
        # а вот место на диске или повреждённый файл отменяют — и об этом надо сказать.
        try:
            async with game.operation:
                folder=backup(data);prune_backups(data)
                # Отчёт уже записан backup(): второй записи здесь не место — её сбой
                # выдал бы «копия не создана» за копию, которая на диске лежит.
                report=None;path=folder/REPORT_NAME
                if path.is_file():
                    try:report=json.loads(path.read_text(encoding='utf-8'))
                    except (OSError,ValueError):report=None
        except ValueError as error:return JSONResponse({'detail':str(error),'code':'backup_refused'},status_code=409)
        except OSError as error:return JSONResponse({'detail':'Не удалось записать копию: '+str(error)+'; профиль не изменён','code':'backup_failed'},status_code=409)
        except sqlite3.Error as error:return JSONResponse({'detail':'Не удалось прочитать базу при копировании: '+str(error)+'; профиль не изменён','code':'backup_failed'},status_code=409)
        return dict(id=folder.name,ok=report is None,broken_links=(report or {}).get('broken_links',0),
                    damaged=bool((report or {}).get('damaged')),
                    summary=(report or {}).get('summary','Копия создана: нарушенных связей нет'),
                    report_file=REPORT_NAME if report else None,report=report)
    @app.get('/api/data/export')
    async def export():
        # Отказ называет причину и код: молчание вместо файла — тот же дефект, что и 409 без слов.
        try:
            async with game.operation:folder=prepare_export(data)
        except ExportRefused as refusal:return JSONResponse({'detail':str(refusal),'code':refusal.code},status_code=409)
        except OSError as error:return JSONResponse({'detail':'Не удалось подготовить скачивание: '+str(error)+'; профиль не изменён','code':'export_failed'},status_code=409)
        except ValueError as error:return JSONResponse({'detail':str(error)+'; профиль не изменён','code':'export_failed'},status_code=409)
        except sqlite3.Error as error:return JSONResponse({'detail':'Не удалось прочитать базу при подготовке скачивания: '+str(error)+'; профиль не изменён','code':'export_failed'},status_code=409)
        try:
            report=integrity_report(folder);archive=write_export(folder)
        except (OSError,ValueError,sqlite3.Error) as error:
            finish_export(folder);return JSONResponse({'detail':'Не удалось собрать архив: '+str(error)+'; профиль не изменён','code':'export_failed'},status_code=409)
        except BaseException:
            finish_export(folder);raise
        broken=(report or {}).get('broken_links',0);damaged=bool((report or {}).get('damaged'))
        # Заголовок читает экран: файл отдан, но внутри есть отчёт о находках.
        links='damaged' if damaged else ('broken' if broken else 'ok')
        headers={'X-Solo-Integrity':'broken_links='+str(broken)+'; damaged='+str(int(damaged))+'; links='+links}
        return FileResponse(archive,filename=EXPORT_NAME,media_type='application/zip',headers=headers,background=BackgroundTask(finish_export,folder))
