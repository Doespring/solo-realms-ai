"""Profile data, coherent SQLite snapshots and explicit legacy import; no installed-tree writes."""
import json,os,sqlite3,uuid,shutil
from pathlib import Path
from datetime import datetime,timezone
from contextlib import closing
from .version import VERSION,SCHEMA_VERSION

MANAGED=('solo.sqlite3','cloud-usage.sqlite3','cloud-profiles.json','cloud-secrets','desktop.json','preparation.sqlite3','adventure.sqlite3','adventure-files')
def profile_dir():
    if os.name!='nt':raise RuntimeError('Desktop поддерживается только в Windows')
    return Path(os.environ['LOCALAPPDATA'])/'SoloRealmsAI'
#: Отчёт о находках кладётся рядом с копией: игрок видит причину, а не пустой список.
REPORT_NAME='integrity-report.json'
def _quoted(name):return '"'+str(name).replace('"','""')+'"'
def foreign_key_problems(db):
    """Каждая запись, чья внешняя связь не находит родителя: таблица, rowid, родитель, ключ.

    `PRAGMA foreign_key_check` отдаёт (таблица, rowid, родитель, номер связи); номер
    переводится в имена колонок через `foreign_key_list`, а значения ключа читаются из
    самой записи. Так находку можно показать человеку, не открывая базу руками.
    """
    found=[]
    for table,rowid,parent,fkid in db.execute('PRAGMA foreign_key_check').fetchall():
        link=dict(table=table,rowid=rowid,parent=parent,foreign_key=fkid,columns=[],values={})
        try:
            columns=sorted((row for row in db.execute('PRAGMA foreign_key_list('+_quoted(table)+')').fetchall() if row[0]==fkid),key=lambda row:row[1])
            link['columns']=[dict(column=row[3],parent_column=row[4]) for row in columns]
            if columns and rowid is not None:
                row=db.execute('SELECT '+','.join(_quoted(item['column']) for item in link['columns'])+' FROM '+_quoted(table)+' WHERE rowid=?',(rowid,)).fetchone()
                if row is not None:link['values']={item['column']:row[index] for index,item in enumerate(link['columns'])}
            elif columns:link['note']='у записи нет rowid (WITHOUT ROWID): значения ключа не прочитаны'
        except sqlite3.DatabaseError as error:link['note']='ключ не прочитан: '+str(error)
        found.append(link)
    return found
def sqlite_health(path):
    """Отчёт о файле: целостность, нарушенные связи, версия схемы. Файл только читается."""
    path=Path(path)
    report=dict(database=path.name,bytes=path.stat().st_size if path.is_file() else 0,
                checked=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                integrity='not_checked',integrity_errors=[],foreign_keys=[],schema=0,campaign=False)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        # Порча страниц валит сам PRAGMA: это тоже «повреждён», а не исключение наружу.
        try:errors=[row[0] for row in db.execute('PRAGMA integrity_check').fetchall()]
        except sqlite3.DatabaseError as error:errors=['файл не читается: '+str(error)]
        if errors!=['ok']:
            report['integrity']='broken';report['integrity_errors']=errors[:50] or ['integrity_check не ответил ok'];report['ok']=False;return report
        report['integrity']='ok'
        report['schema']=db.execute('PRAGMA user_version').fetchone()[0]
        report['campaign']=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='campaigns' AND type='table'").fetchone())
        report['foreign_keys']=foreign_key_problems(db)
    report['ok']=report['integrity']=='ok' and not report['foreign_keys']
    return report
def health_summary(health):
    """Одна строка по-русски: что именно нашлось в файле."""
    if health['integrity']!='ok':return 'Файл '+health['database']+' повреждён: '+'; '.join(health['integrity_errors'][:3])
    broken=len(health['foreign_keys'])
    if broken:
        tables=', '.join(sorted({item['table'] for item in health['foreign_keys']})[:5])
        return 'Файл '+health['database']+': нарушено внешних связей — '+str(broken)+' (таблицы: '+tables+'); записи не удалены'
    return 'Файл '+health['database']+': целостность в порядке'
def check_db(path,*,campaign=False):
    """Проверка ГОТОВОЙ копии: повреждение — отказ, нарушенные связи — отчёт.

    Нарушенная внешняя связь не повод не делать копию: место такой находки — в отчёте
    рядом с копией. Жёстко проверяется только то, что делает файл ЧУЖИМ или слишком
    новым, — версия схемы и наличие базы кампаний.
    """
    health=sqlite_health(path)
    if health['integrity']!='ok':raise ValueError('SQLite повреждён: '+'; '.join(health['integrity_errors'][:3])+'; исходный файл не изменён')
    if campaign:
        if health['schema']>SCHEMA_VERSION:raise ValueError('Сохранения требуют более новой версии')
        if not health['campaign']:raise ValueError('Это не база кампаний Solo Realms AI')
    return health
def integrity_report(folder):
    """Отчёт рядом с копией. Пишется только когда есть находки; иначе нечего показывать."""
    folder=Path(folder);meta=folder/'snapshot.json'
    if not meta.is_file():return None
    files=json.loads(meta.read_text(encoding='utf-8')).get('health') or {}
    if not files:return None
    report=dict(created=datetime.now(timezone.utc).isoformat(timespec='seconds'),copy=folder.name,
                summary='; '.join(health_summary(item) for item in files.values()),
                damaged=any(item['integrity']!='ok' for item in files.values()),
                broken_links=sum(len(item['foreign_keys']) for item in files.values()),files=files)
    (folder/REPORT_NAME).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
def snapshot(source,target,*,allow_damaged=False):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target:raise ValueError('Источник и копия совпадают')
    target.mkdir(parents=True,exist_ok=False)
    copied=[];health={}
    for name in MANAGED:
        src=source/name;dst=target/name
        if not src.exists():continue
        if src.is_symlink():raise ValueError('Ссылки в импорте данных не поддерживаются')
        if name.endswith('.sqlite3'):
            # Снимок делается ДО проверок: сырая копия через backup API. Проверки читают
            # КОПИЮ, живая база только читается — поэтому нарушенная связь не отменяет копию.
            with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as read,closing(sqlite3.connect(dst)) as write:
                read.backup(write)
                # Копия повреждённой базы может не принять смену журнала: страницы уже
                # сняты, и файл всё равно уедет с пометкой «повреждён», а не пропадёт.
                try:write.execute('PRAGMA journal_mode=DELETE')
                except sqlite3.DatabaseError:pass
            found=sqlite_health(dst)
            # Повреждение файла — отказ: такую копию не восстановить (check_db и
            # preview её отвергнут), значит называть её резервной нельзя. Нарушенные
            # связи — наоборот, находка в отчёт. allow_damaged=True есть только у
            # «Экспорта данных»: там сырой снимок уезжает к игроку вместе с причиной.
            if found['integrity']!='ok':
                if not allow_damaged:raise ValueError('SQLite повреждён: '+'; '.join(found['integrity_errors'][:3])+'; исходный файл не изменён')
            elif name=='solo.sqlite3':
                if found['schema']>SCHEMA_VERSION:raise ValueError('Сохранения требуют более новой версии')
                if not found['campaign']:raise ValueError('Это не база кампаний Solo Realms AI')
            if not found['ok']:health[name]=found
        elif name=='adventure-files':
            import re
            dst.mkdir()
            for item in src.iterdir():
                if item.is_symlink():raise ValueError('Ссылки в библиотеке не поддерживаются')
                if not re.fullmatch(r'[0-9a-f]{64}(?:\.bin|-p\d{1,4}-w\d{1,4}\.(?:png|svg))',item.name):continue
                if not item.is_file() or item.stat().st_size>256*1024*1024:raise ValueError('Некорректный файл библиотеки')
                shutil.copy2(item,dst/item.name)
        elif name=='cloud-secrets':
            dst.mkdir()
            for item in src.glob('*.dpapi'):
                uuid.UUID(item.stem)
                if item.is_symlink() or item.stat().st_size>65536:raise ValueError('Некорректный файл ключа')
                shutil.copy2(item,dst/item.name)
        else:
            if src.stat().st_size>2000000:raise ValueError('Файл настроек слишком велик')
            raw=json.loads(src.read_text(encoding='utf-8'))
            if name=='cloud-profiles.json':
                from .cloud_settings import CloudProfile
                for p in raw.get('profiles',[]):CloudProfile.model_validate(p)
            shutil.copy2(src,dst)
        copied.append(name)
    (target/'snapshot.json').write_text(json.dumps(dict(version=VERSION,files=copied,created=datetime.now(timezone.utc).isoformat(),health=health),ensure_ascii=False,indent=2),encoding='utf-8')
    return copied
def backup(data,label='manual'):
    data=Path(data).resolve();folder=data/'backups'/(label+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    folder.parent.mkdir(parents=True,exist_ok=True)
    # Отказ после снимка (чужая база, схема новее) не оставляет огрызок в списке копий.
    try:snapshot(data,folder)
    except BaseException:
        shutil.rmtree(folder,ignore_errors=True);raise
    integrity_report(folder);return folder
def import_legacy(source,data):
    source,data=Path(source).resolve(),Path(data).resolve()
    if source==data or not (source/'solo.sqlite3').is_file():raise ValueError('Выберите исходный каталог data с базой кампаний')
    data.mkdir(parents=True,exist_ok=True)
    if any((data/n).exists() for n in MANAGED):raise ValueError('В профиле уже есть данные: импорт не перезаписывает их')
    staged=data/('.import-'+uuid.uuid4().hex)
    try:copied=snapshot(source,staged)
    except BaseException:
        shutil.rmtree(staged,ignore_errors=True);raise
    moved=[]
    try:
        for name in copied:
            if (data/name).exists():raise ValueError('Профиль изменился во время импорта')
            (staged/name).rename(data/name);moved.append(name)
    except BaseException:
        for name in reversed(moved):(data/name).rename(staged/name)
        raise
    (staged/'snapshot.json').rename(data/'legacy-import.json');staged.rmdir()
    return copied
def prepare(data):
    data=Path(data);data.mkdir(parents=True,exist_ok=True);marker=data/'app-version.json';old=json.loads(marker.read_text(encoding='utf-8')) if marker.exists() else {}
    path=data/'solo.sqlite3'
    if path.exists():
        check_db(path,campaign=True)
        if old.get('version')!=VERSION:backup(data,'before-update')
    return lambda: marker.write_text(json.dumps(dict(version=VERSION,schema=SCHEMA_VERSION)),encoding='utf-8')

class ProfileLock:
    def __init__(self,data):self.path=Path(data)/'desktop.lock';self.file=None
    def __enter__(self):
        import msvcrt
        self.path.parent.mkdir(parents=True,exist_ok=True);self.file=self.path.open('a+b');self.file.seek(0)
        if not self.path.stat().st_size:self.file.write(b'0');self.file.flush()
        self.file.seek(0)
        try:msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:
            self.file.close();self.file=None;raise RuntimeError('Приложение уже открыто с этим профилем данных')
        return self
    def __exit__(self,*args):
        import msvcrt
        if self.file:self.file.seek(0);msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1);self.file.close();self.file=None

class InstallMutex:
    """Signals Inno Setup that app binaries are in use; profile locking is separate."""
    def __enter__(self):
        import ctypes
        self.api=ctypes.WinDLL('kernel32',use_last_error=True);self.api.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p];self.api.CreateMutexW.restype=ctypes.c_void_p
        self.handle=self.api.CreateMutexW(None,False,'SoloRealmsAI.Desktop')
        if not self.handle:raise OSError(ctypes.get_last_error(),'Не удалось создать mutex приложения')
        return self
    def __exit__(self,*args):
        import ctypes
        self.api.CloseHandle.argtypes=[ctypes.c_void_p];self.api.CloseHandle.restype=ctypes.c_bool;self.api.CloseHandle(self.handle)
