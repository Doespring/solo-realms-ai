import importlib.util,json,hashlib,sqlite3
from pathlib import Path
from contextlib import closing
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app,ROOT
from solo.storage import Store
from solo.contracts import Hero
from solo.llm import MockLLM
from solo.visual_assets import registry
from solo.data_management import plan_restore,consume_restore,preview,candidate

spec=importlib.util.spec_from_file_location('visual_import',ROOT/'scripts/import_visual_assets.py');pipeline=importlib.util.module_from_spec(spec);spec.loader.exec_module(pipeline)

def test_catalog_exactly_preserved_and_registry_is_not_campaign_database():
    sources=pipeline.verify_catalog();assert len(sources)==31
    catalog=registry(ROOT);assert len(catalog['assets'])==65
    with closing(sqlite3.connect(ROOT/'visual_catalog/assets.sqlite3')) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name IN ('campaigns','events','actions')").fetchall()
        reasons=dict(db.execute('SELECT source_id,status FROM source_reviews'));assert reasons['excluded-2min']=='rejected' and reasons['portraits-collection']=='deferred'
    for asset in catalog['assets']:
        path=ROOT/'web'/asset['path'];assert path.resolve().is_relative_to(ROOT/'web/visual') and hashlib.sha256(path.read_bytes()).hexdigest()==asset['sha256']
        assert (ROOT/'web'/asset['thumbnail']).exists() and asset['author'] and asset['license'] in pipeline.LICENSES

@pytest.mark.parametrize('payload',[
 b'<svg xmlns="http://www.w3.org/2000/svg"><script>1</script></svg>',
 b'<svg xmlns="http://www.w3.org/2000/svg" onload="x()"/>',
 b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.test/x"/></svg>',
 b'<!DOCTYPE svg [<!ENTITY x "bad">]><svg>&x;</svg>',
 b'<svg xmlns="http://www.w3.org/2000/svg"><path style="fill:url(https://evil.test)"/></svg>'])
def test_external_or_executable_svg_not_imported(payload):
    with pytest.raises(ValueError):pipeline.normalize_svg(payload)

def test_observed_icons_are_monochrome_and_background_is_transparent():
    value=pipeline.normalize_svg(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><path d="M0 0h512v512H0z"/><path fill="#fff" d="M1 1h4v4z"/></svg>')
    assert b'M0 0h512' not in value and b'#202126' in value and value.count(b'<path')==1

def test_restore_is_planned_then_applied_offline_and_current_copy_kept(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');cid=store.create('Исходная кампания',Hero(name='Герой'))['id']
    from solo.user_data import backup
    earlier=backup(tmp_path)
    with store.connection(True) as c:c.execute('UPDATE campaigns SET name=? WHERE id=?',('После изменения',cid))
    result=plan_restore(tmp_path,earlier.name)
    assert Store(store.path).campaign(cid)['name']=='После изменения' and preview(tmp_path,earlier.name)['integrity']=='ok'
    assert Store(tmp_path/'backups'/result['safety']/'solo.sqlite3').campaign(cid)['name']=='После изменения'
    assert consume_restore(tmp_path)
    assert Store(store.path).campaign(cid)['name']=='Исходная кампания' and not consume_restore(tmp_path)
    assert list(tmp_path.glob('.rollback-*'))

def test_bad_restore_does_not_change_live_campaign_and_traversal_rejected(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');cid=store.create('История',Hero(name='Герой'))['id'];state=store.campaign(cid)
    with pytest.raises(ValueError):candidate(tmp_path,'../other')
    bad=tmp_path/'backups/bad';bad.mkdir(parents=True);(bad/'solo.sqlite3').write_bytes(b'not SQLite')
    with pytest.raises(sqlite3.DatabaseError):plan_restore(tmp_path,'bad')
    assert Store(store.path).campaign(cid)==state and not (tmp_path/'pending-restore.json').exists()

def test_local_asset_requests_licenses_and_data_export_need_no_external_network(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');cid=store.create('Тест',Hero(name='Герой'))['id']
    with TestClient(create_app(store.path,MockLLM())) as client:
        assert client.get('/api/visual-assets').status_code==200
        for path in ['/static/visual/icons/book.svg','/static/visual/fonts/book-regular.ttf','/static/visual/frames/frame-2.png','/static/visual/tiles/token-yellow.png','/static/visual/ATTRIBUTION.md']:assert client.get(path).status_code==200
        before=store.campaign(cid);result=client.post('/api/data/backup',json={});assert result.status_code==200
        assert client.get('/api/data/backups/'+result.json()['id']+'/preview').json()['campaigns']==1
        exported=client.get('/api/data/export');assert exported.status_code==200
        import io,zipfile
        with zipfile.ZipFile(io.BytesIO(exported.content)) as z:assert 'solo.sqlite3' in z.namelist() and not any('cloud-secrets' in n for n in z.namelist()) and z.testzip() is None
        assert store.campaign(cid)==before
