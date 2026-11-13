"""A copy is made even when the database has broken foreign keys.

The snapshot is written first through the SQLite backup API and only then checked —
on the copy, never on the live profile. Findings go to a report next to the copy
(table, rowid, parent of every orphaned row), and nothing is deleted.
"""
import hashlib,io,json,sqlite3,zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solo.app import create_app
from solo.contracts import Hero
from solo.data_management import consume_restore,plan_restore,prepare_export,preview,write_export
from solo.user_data import integrity_report
from solo.llm import MockLLM
from solo.storage import Store
from solo.user_data import REPORT_NAME,backup,check_db,prepare,sqlite_health

ORPHAN='нет-такой-кампании'

def broken_profile(tmp_path):
    """Профиль с одной записью, чья внешняя связь не находит родителя."""
    store=Store(tmp_path/'solo.sqlite3')
    cid=store.create('Кампания',Hero(name='Герой'))['id']
    # foreign_keys выключены у обычного соединения — так и появляется сирота.
    with sqlite3.connect(store.path) as db:
        db.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)',('chk-orphan',ORPHAN,'Сирота','{}',0,0.0,'[]'))
    return store,cid

def live_md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()

def test_copy_is_made_and_reports_table_rowid_and_parent(tmp_path):
    """Приёмка 1 и 3: файл создаётся (размер > 0), в ответе — таблица, rowid, родитель."""
    store,cid=broken_profile(tmp_path)
    with TestClient(create_app(store.path,MockLLM())) as client:
        answer=client.post('/api/data/backup',json={})
        assert answer.status_code==200,answer.text
        body=answer.json()
        folder=tmp_path/'backups'/body['id']
        copy=folder/'solo.sqlite3'
        assert copy.is_file() and copy.stat().st_size>0,copy
        assert body['broken_links']==1,body
        orphan=body['report']['files']['solo.sqlite3']['foreign_keys'][0]
        assert orphan['table']=='checkpoints',orphan
        assert orphan['rowid']==1,orphan
        assert orphan['parent']=='campaigns',orphan
        assert orphan['columns']==[{'column':'campaign','parent_column':'id'}],orphan
        assert orphan['values']=={'campaign':ORPHAN},orphan
        # Список копий больше не пуст, и находка видна в самом списке.
        listed=client.get('/api/data/backups').json()['backups']
        assert [item['id'] for item in listed]==[body['id']],listed
        assert listed[0]['broken_links']==1,listed
        # Копия полная: данные кампании на месте.
        assert Store(copy).campaign(cid)['name']=='Кампания'

def test_live_database_is_not_changed_by_the_copy(tmp_path):
    """Приёмка 2: md5 исходной базы до и после совпадает.

    Отсчёт берётся после старта приложения: старт сам пишет миграции, и это не
    результат копирования. Дальше ни копия, ни скачивание не меняют файл.
    """
    store,cid=broken_profile(tmp_path)
    with TestClient(create_app(store.path,MockLLM())) as client:
        before=live_md5(store.path)
        assert client.post('/api/data/backup',json={}).status_code==200
        assert client.get('/api/data/export').status_code==200
        assert live_md5(store.path)==before
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT campaign FROM checkpoints WHERE id='chk-orphan'").fetchone()==(ORPHAN,),'сироту нельзя удалять'
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[('checkpoints',1,'campaigns',0)]

def test_report_file_next_to_copy_names_every_broken_record(tmp_path):
    """Отчёт рядом с копией: таблица, rowid, родитель и русское объяснение."""
    store,cid=broken_profile(tmp_path)
    folder=backup(tmp_path,'manual')
    report=json.loads((folder/REPORT_NAME).read_text(encoding='utf-8'))
    assert report['copy']==folder.name and report['broken_links']==1,report
    assert 'checkpoints' in report['summary'] and 'не удалены' in report['summary'],report['summary']
    orphan=report['files']['solo.sqlite3']['foreign_keys'][0]
    assert (orphan['table'],orphan['rowid'],orphan['parent'])==('checkpoints',1,'campaigns'),orphan
    assert report['files']['solo.sqlite3']['integrity']=='ok',report

def test_clean_database_leaves_no_report(tmp_path):
    """Отчёт не появляется там, где нечего показать: чистая копия остаётся чистой."""
    store=Store(tmp_path/'solo.sqlite3');store.create('Кампания',Hero(name='Герой'))
    folder=backup(tmp_path,'manual')
    assert not (folder/REPORT_NAME).exists()
    health=sqlite_health(folder/'solo.sqlite3')
    assert health['integrity']=='ok' and health['foreign_keys']==[] and health['ok'] is True,health

def test_restore_from_diagnostic_copy_passes_integrity_check_and_keeps_orphan(tmp_path):
    """Приёмка 4: восстановление из снимка проходит, integrity_check = ok, сирота на месте."""
    store,cid=broken_profile(tmp_path)
    with TestClient(create_app(store.path,MockLLM())) as client:
        copy_id=client.post('/api/data/backup',json={}).json()['id']
        checked=client.get('/api/data/backups/'+copy_id+'/preview').json()
        assert checked['campaigns']==1 and checked['integrity']=='ok',checked
        assert checked['warning'] and 'ничего не удаляя' in checked['warning'],checked
    planned=plan_restore(tmp_path,copy_id)
    assert planned['status']=='restart_required' and planned['warning'],planned
    assert consume_restore(tmp_path) is True
    with sqlite3.connect(store.path) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert db.execute("SELECT campaign FROM checkpoints WHERE id='chk-orphan'").fetchone()==(ORPHAN,),'восстановление не удаляет сирот'
    assert Store(store.path).campaign(cid)['name']=='Кампания'

def test_export_with_broken_links_ships_the_report_inside_the_archive(tmp_path):
    """Приёмка 5 (файл + причина): скачивание отдаёт ZIP с базой и отчётом."""
    store,cid=broken_profile(tmp_path)
    with TestClient(create_app(store.path,MockLLM())) as client:
        exported=client.get('/api/data/export')
        assert exported.status_code==200,exported.text
        assert exported.headers['X-Solo-Integrity']=='broken_links=1; damaged=0; links=broken',exported.headers
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            names=archive.namelist()
            assert 'solo.sqlite3' in names and REPORT_NAME in names,names
            report=json.loads(archive.read(REPORT_NAME).decode('utf-8'))
            assert report['broken_links']==1 and report['files']['solo.sqlite3']['foreign_keys'][0]['parent']=='campaigns',report
            assert archive.testzip() is None
    assert not (tmp_path/'.exports').exists() or not any((tmp_path/'.exports').iterdir())
    assert not (tmp_path/'backups').exists() or not any((tmp_path/'backups').iterdir()),'скачивание не создаёт копию'

def test_export_refusal_names_a_code(tmp_path,monkeypatch):
    """Приёмка 5 (отказ с кодом): причина по-русски и код, а не молчание."""
    store=Store(tmp_path/'solo.sqlite3');store.create('Кампания',Hero(name='Герой'))
    monkeypatch.setattr('solo.data_management.MAX_EXPORT_BYTES',1)
    with TestClient(create_app(store.path,MockLLM())) as client:
        refused=client.get('/api/data/export')
        assert refused.status_code==409,refused.text
        body=refused.json()
        assert body['code']=='export_too_large',body
        assert 'лимита скачивания' in body['detail'],body
    assert not (tmp_path/'.exports').exists() or not any((tmp_path/'.exports').iterdir())

def test_broken_links_do_not_stop_the_start_of_the_app(tmp_path):
    """Доменная валидация отделена от резервирования: база со сиротой не мешает запуску."""
    store,cid=broken_profile(tmp_path)
    marker=prepare(tmp_path)
    marker()
    assert json.loads((tmp_path/'app-version.json').read_text(encoding='utf-8'))['version']
    health=check_db(store.path,campaign=True)
    assert health['integrity']=='ok' and health['ok'] is False and len(health['foreign_keys'])==1,health

def test_refused_copy_leaves_no_half_made_folder_in_the_list(tmp_path):
    """Отказ после снимка (схема новее) не оставляет папку, которую список выдаст за копию."""
    store=Store(tmp_path/'solo.sqlite3');store.create('Кампания',Hero(name='Герой'))
    with sqlite3.connect(store.path) as db:db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError,match='более новой версии'):backup(tmp_path,'manual')
    folder=tmp_path/'backups'
    assert not folder.exists() or not any(folder.iterdir()),(list(folder.iterdir()) if folder.exists() else [])

@pytest.mark.parametrize('error,code',[(ValueError('Сохранения требуют более новой версии'),'backup_refused'),(sqlite3.OperationalError('database is locked'),'backup_failed'),(OSError('No space left on device'),'backup_failed')])
def test_backup_refusal_names_a_code_and_a_russian_reason(tmp_path,monkeypatch,error,code):
    """Копия не сделана — причина по-русски и код, а не 500 и не пустой список без слов."""
    store=Store(tmp_path/'solo.sqlite3');store.create('Кампания',Hero(name='Герой'))
    def refuse(*args,**kwargs):raise error
    monkeypatch.setattr('solo.data_management.backup',refuse)
    with TestClient(create_app(store.path,MockLLM())) as client:
        refused=client.post('/api/data/backup',json={})
        assert refused.status_code==409,refused.text
        body=refused.json()
        assert body['code']==code,body
        assert body['detail'] and body['detail'][0].isupper(),body
        assert 'Internal Server Error' not in refused.text
        assert client.get('/api/data/backups').json()['backups']==[],'недоделанная копия не попадает в список'

@pytest.mark.parametrize('error',[ValueError('Ссылки в импорте данных не поддерживаются'),sqlite3.OperationalError('database is locked')])
def test_export_refusal_always_carries_a_code(tmp_path,monkeypatch,error):
    """Приёмка 5 (отказ с кодом): любая ошибка подготовки — причина по-русски и код, не 500."""
    store=Store(tmp_path/'solo.sqlite3');store.create('Кампания',Hero(name='Герой'))
    def refuse(*args,**kwargs):raise error
    monkeypatch.setattr('solo.data_management.snapshot',refuse)
    with TestClient(create_app(store.path,MockLLM())) as client:
        refused=client.get('/api/data/export')
        assert refused.status_code==409,refused.text
        body=refused.json()
        assert body['code']=='export_failed',body
        assert 'профиль не изменён' in body['detail'],body
    assert not (tmp_path/'.exports').exists() or not any((tmp_path/'.exports').iterdir())



def damaged_profile(tmp_path):
    """База, которую SQLite ещё читает, но `integrity_check` уже отвергает.

    Повреждённая схема (индекс ссылается на несуществующую колонку) — ровно тот
    случай, когда копия через backup API снимается, а восстановить её нельзя.
    """
    store=Store(tmp_path/'solo.sqlite3');store.create('Кампания',Hero(name='Герой'))
    with sqlite3.connect(store.path) as db:
        db.execute('PRAGMA writable_schema=ON')
        db.execute("UPDATE sqlite_master SET sql='CREATE INDEX idx_broken ON campaigns(nosuch)' WHERE type='index'")
    return store


def test_damaged_database_is_refused_not_called_a_backup(tmp_path):
    """Порча файла — отказ: копию, которую нельзя восстановить, списком копий не выдают."""
    store=damaged_profile(tmp_path)
    with pytest.raises(ValueError,match='повреждён'):backup(tmp_path,'manual')
    folder=tmp_path/'backups'
    assert not folder.exists() or not any(folder.iterdir()),(list(folder.iterdir()) if folder.exists() else [])
    health=sqlite_health(store.path)
    assert health['integrity']=='broken' and health['ok'] is False,health


def test_export_of_damaged_database_ships_file_and_reason(tmp_path):
    """Приёмка 5: повреждение не превращается в молчание — файл едет с причиной в заголовке."""
    store=Store(tmp_path/'solo.sqlite3');store.create('Кампания',Hero(name='Герой'))
    with TestClient(create_app(store.path,MockLLM())) as client:
        # Портим файл под работающим приложением: так повреждение и приходит к игроку.
        with sqlite3.connect(store.path) as db:
            db.execute('PRAGMA writable_schema=ON')
            db.execute("UPDATE sqlite_master SET sql='CREATE INDEX idx_broken ON campaigns(nosuch)' WHERE type='index'")
        refused=client.post('/api/data/backup',json={})
        assert refused.status_code==409,refused.text
        assert refused.json()['code']=='backup_refused',refused.json()
        exported=client.get('/api/data/export')
        assert exported.status_code==200,exported.text
        assert exported.headers['X-Solo-Integrity']=='broken_links=0; damaged=1; links=damaged',exported.headers
        with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
            report=json.loads(archive.read(REPORT_NAME).decode('utf-8'))
            assert report['damaged'] is True,report
            assert 'повреждён' in report['summary'],report['summary']
            assert 'solo.sqlite3' in archive.namelist()


def test_archive_failure_after_the_copy_names_a_code(tmp_path,monkeypatch):
    """Сбой сборки ZIP — отказ с кодом и русской причиной, а не 500 без слов."""
    store=Store(tmp_path/'solo.sqlite3');store.create('Кампания',Hero(name='Герой'))
    def refuse(*args,**kwargs):raise OSError(28,'No space left on device')
    monkeypatch.setattr('solo.data_management.write_export',refuse)
    with TestClient(create_app(store.path,MockLLM())) as client:
        refused=client.get('/api/data/export')
        assert refused.status_code==409,refused.text
        body=refused.json()
        assert body['code']=='export_failed' and 'Не удалось собрать архив' in body['detail'],body
    assert not (tmp_path/'.exports').exists() or not any((tmp_path/'.exports').iterdir())


def test_planned_restore_keeps_the_chosen_copy_when_the_quota_is_full(tmp_path):
    """Выбранная копия не вытесняется страховочной: иначе приложение не открывается.

    При пяти копиях восстановление самой старой раньше удаляло ровно её, а
    pending-restore.json оставался — следующее открытие падало на «Копия не найдена».
    """
    store=Store(tmp_path/'solo.sqlite3');cid=store.create('Кампания',Hero(name='Герой'))['id']
    copies=[backup(tmp_path,'manual').name for _ in range(5)]
    chosen=min(copies,key=lambda name:(tmp_path/'backups'/name).stat().st_mtime_ns)
    planned=plan_restore(tmp_path,chosen)
    assert (tmp_path/'backups'/chosen/'solo.sqlite3').is_file(),'выбранная копия удалена уборкой'
    assert json.loads((tmp_path/'pending-restore.json').read_text(encoding='utf-8'))['backup']==chosen
    assert planned['safety'] and (tmp_path/'backups'/planned['safety']).is_dir()
    assert consume_restore(tmp_path) is True
    assert Store(tmp_path/'solo.sqlite3').campaign(cid)['name']=='Кампания'


def test_export_report_and_database_travel_together(tmp_path):
    """Отчёт собирается один раз и лежит в том же снимке, из которого строится ZIP."""
    broken_profile(tmp_path)
    folder=prepare_export(tmp_path)
    report=json.loads((folder/REPORT_NAME).read_text(encoding='utf-8'))
    assert report['broken_links']==1 and report['damaged'] is False,report
    assert integrity_report(folder)['broken_links']==1
    archive=write_export(folder)
    with zipfile.ZipFile(archive) as bundle:
        assert REPORT_NAME in bundle.namelist()


def test_successful_restore_leaves_no_staging_folder(tmp_path):
    """Каркас переноса убирается, страховочная копия остаётся: мусор не растёт с каждым восстановлением."""
    store,cid=broken_profile(tmp_path)
    copy=backup(tmp_path,'manual')
    plan_restore(tmp_path,copy.name)
    assert consume_restore(tmp_path) is True
    assert not list(tmp_path.glob('.restore-*')),'пустой каркас переноса остался в профиле'
    assert list(tmp_path.glob('.rollback-*')),'страховка профиля должна остаться'
    assert not (tmp_path/'pending-restore.json').exists()
    assert Store(tmp_path/'solo.sqlite3').campaign(cid)['name']=='Кампания'

def test_restore_plan_is_written_atomically(tmp_path):
    """План пишется заменой: оборванная запись не оставит половину JSON."""
    store,cid=broken_profile(tmp_path)
    copy=backup(tmp_path,'manual')
    plan_restore(tmp_path,copy.name)
    assert not list(tmp_path.glob('pending-restore.json.tmp'))
    json.loads((tmp_path/'pending-restore.json').read_text(encoding='utf-8'))
