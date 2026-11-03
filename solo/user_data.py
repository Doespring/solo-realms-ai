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
def check_db(path,*,campaign=False):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLite повреждён; исходный файл не изменён')
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('SQLite содержит нарушенные связи')
        if campaign:
            if db.execute('PRAGMA user_version').fetchone()[0]>SCHEMA_VERSION:raise ValueError('Сохранения требуют более новой версии')
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='campaigns' AND type='table'").fetchone():raise ValueError('Это не база кампаний Solo Realms AI')
def snapshot(source,target):
    source,target=Path(source).resolve(),Path(target).resolve()
    if source==target:raise ValueError('Источник и копия совпадают')
    target.mkdir(parents=True,exist_ok=False)
    copied=[]
    for name in MANAGED:
        src=source/name;dst=target/name
        if not src.exists():continue
        if src.is_symlink():raise ValueError('Ссылки в импорте данных не поддерживаются')
        if name.endswith('.sqlite3'):
            check_db(src,campaign=name=='solo.sqlite3')
            with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as read,closing(sqlite3.connect(dst)) as write:
                read.backup(write);write.execute('PRAGMA journal_mode=DELETE')
            check_db(dst,campaign=name=='solo.sqlite3')
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
    (target/'snapshot.json').write_text(json.dumps(dict(version=VERSION,files=copied,created=datetime.now(timezone.utc).isoformat()),ensure_ascii=False,indent=2),encoding='utf-8')
    return copied
def backup(data,label='manual'):
    data=Path(data).resolve();folder=data/'backups'/(label+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    folder.parent.mkdir(parents=True,exist_ok=True);snapshot(data,folder);return folder
def import_legacy(source,data):
    source,data=Path(source).resolve(),Path(data).resolve()
    if source==data or not (source/'solo.sqlite3').is_file():raise ValueError('Выберите исходный каталог data с базой кампаний')
    data.mkdir(parents=True,exist_ok=True)
    if any((data/n).exists() for n in MANAGED):raise ValueError('В профиле уже есть данные: импорт не перезаписывает их')
    staged=data/('.import-'+uuid.uuid4().hex);copied=snapshot(source,staged)
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
