import copy,json,sqlite3,uuid,os
from pathlib import Path
from fastapi.testclient import TestClient
import pytest
from solo.storage import Store
from solo.contracts import Hero
from solo.app import create_app
from solo.llm import MockLLM
from solo.cloud_settings import WindowsKeyVault,CloudSettings,CloudProfile
from solo.user_data import snapshot,import_legacy,prepare,backup,ProfileLock,profile_dir
from solo.desktop_host import DesktopHost,WindowPreferences,register_desktop

def source(tmp_path):
    folder=tmp_path/'source';store=Store(folder/'solo.sqlite3');cid=store.create('Сохранённая история',Hero(name='Тестовый герой'))['id']
    store.save(cid,'Начальная точка')
    return folder,store,cid

def test_legacy_import_coherent_databases_profiles_dpapi_and_no_runtime(tmp_path):
    folder,store,cid=source(tmp_path);profile=CloudProfile(base_url='https://example.test/v1');settings=CloudSettings(folder/'cloud-profiles.json');settings.save(profile)
    settings.vault.set(profile.id,'synthetic-test-key')
    from solo.cloud_cost import CostLedger
    costs=CostLedger(folder/'cloud-usage.sqlite3')
    (folder/'app.stderr.log').write_text('PRIVATE LOG');(folder/'qa').mkdir();(folder/'runtime').mkdir()
    destination=tmp_path/'profile';copied=import_legacy(folder,destination)
    assert Store(destination/'solo.sqlite3').campaign(cid)==store.campaign(cid)
    assert Store(destination/'solo.sqlite3').saves(cid)==store.saves(cid)
    assert CloudSettings(destination/'cloud-profiles.json').vault.get(profile.id)=='synthetic-test-key'
    assert 'synthetic-test-key' not in (destination/'cloud-secrets'/f'{profile.id}.dpapi').read_bytes().decode(errors='ignore')
    assert not (destination/'runtime').exists() and not (destination/'app.stderr.log').exists() and not (destination/'qa').exists()
    with pytest.raises(ValueError):import_legacy(folder,destination)
    assert Store(destination/'solo.sqlite3').campaign(cid)==store.campaign(cid)

def test_backup_contains_committed_wal_state_not_partial_files(tmp_path):
    folder,store,cid=source(tmp_path)
    with store.connection() as c:
        c.execute('PRAGMA journal_mode=WAL');c.execute('UPDATE campaigns SET name=? WHERE id=?',('Новое имя',cid));c.commit()
        copy_folder=backup(folder)
    assert Store(copy_folder/'solo.sqlite3').campaign(cid)['name']=='Новое имя'
    assert not (copy_folder/'solo.sqlite3-wal').exists()

def test_newer_or_corrupt_db_not_migrated_and_original_preserved(tmp_path):
    folder,store,cid=source(tmp_path)
    with sqlite3.connect(store.path) as db:db.execute('PRAGMA user_version=99')
    original=store.path.read_bytes()
    with pytest.raises(ValueError):prepare(folder)
    with pytest.raises(ValueError):import_legacy(folder,tmp_path/'new-profile')
    assert store.path.read_bytes()==original
    bad=tmp_path/'broken';bad.mkdir();(bad/'solo.sqlite3').write_bytes(b'not a db')
    with pytest.raises(sqlite3.DatabaseError):prepare(bad)
    assert (bad/'solo.sqlite3').read_bytes()==b'not a db'

def test_preupdate_checkpoint_once_and_backups_preserve_campaign(tmp_path):
    folder,store,cid=source(tmp_path);(folder/'app-version.json').write_text('{"version":"1.7"}')
    marker=prepare(folder);snapshots=[p for p in (folder/'backups').iterdir() if p.is_dir()];assert len(snapshots)==1
    assert Store(snapshots[0]/'solo.sqlite3').campaign(cid)==store.campaign(cid)
    marker();prepare(folder)();assert len([p for p in (folder/'backups').iterdir() if p.is_dir()])==1

@pytest.mark.skipif(os.name!='nt',reason='Windows profile lock')
def test_profile_lock_cannot_run_two_instances_and_releases(tmp_path,monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path));assert profile_dir()==tmp_path/'SoloRealmsAI'
    with ProfileLock(tmp_path):
        with pytest.raises(RuntimeError):
            with ProfileLock(tmp_path):pass
    with ProfileLock(tmp_path):pass

class Window:
    def __init__(self):self.size=(1280,800);self.fullscreen=False
    def resize(self,w,h):self.size=(w,h)
    def toggle_fullscreen(self):self.fullscreen=not self.fullscreen

def test_session_bootstrap_one_use_cookie_origin_and_bound_native_controls(tmp_path):
    folder,store,cid=source(tmp_path);app=create_app(store.path,MockLLM(),local_port=19161);host=DesktopHost(folder);host.window=Window();register_desktop(app,host,app.state.game)
    token=host.bootstrap
    with TestClient(app) as client:
        assert client.get('/api/campaigns').status_code==403
        assert client.get('/desktop/bootstrap?token=wrong').status_code==403
        response=client.get('/desktop/bootstrap?token='+token,follow_redirects=False)
        assert response.status_code==303 and 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
        assert client.get('/api/campaigns').status_code==200
        assert client.get('/desktop/bootstrap?token='+token).status_code==403
        body=WindowPreferences(width=1024,height=720).model_dump()
        assert client.post('/api/desktop/preferences',json=body,headers={'Origin':'https://evil.test'}).status_code==403
        assert client.post('/api/desktop/preferences',json={**body,'width':4000}).status_code==422
        assert client.post('/api/desktop/preferences',json=body).status_code==200 and host.window.size==(1024,720)
        assert client.post('/api/desktop/preferences',json={**body,'fullscreen':True}).status_code==200 and host.window.fullscreen
        assert client.post('/api/desktop/preferences',json={**body,'port':5000}).status_code==409
        assert client.post('/api/desktop/backup',json={}).status_code==200
        assert Store(store.path).campaign(cid)['state']['hero']['name']=='Тестовый герой'
    assert DesktopHost(folder).preferences.fullscreen

def test_plain_browser_does_not_show_fake_desktop_controls(tmp_path):
    folder,store,cid=source(tmp_path)
    with TestClient(create_app(store.path,MockLLM())) as client:assert client.get('/api/desktop/status').json()=={'available':False}
