"""A download leaves one temporary copy, copies are quota-bounded, and a preview
that outgrows its memory budget dies as a preview instead of taking the backend."""
import io,time,zipfile
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from solo.adventure_library import AdventureLibrary,ImportJobs
from solo.app import create_app
from solo.contracts import Hero
from solo.data_management import prune_backups
from solo.llm import MockLLM
from solo.storage import Store
from solo.user_data import backup

def page_pdf(path,pixels=(300,400)):
    Image.new('RGB',pixels,'white').save(path,'PDF')
    return path

def library_with_page(tmp_path,pixels=(300,400)):
    library=AdventureLibrary(tmp_path/'library')
    source=page_pdf(tmp_path/'page.pdf',pixels)
    sha=library.put_file(source)
    with library.db() as db:
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)',(sha,'.pdf',source.stat().st_size,'extracted',1,'{}'))
        db.execute('INSERT INTO materials(id,document,page,kind,status) VALUES(?,?,?,?,?)',('material-1',sha,1,'page','page'))
    return library

def copies(folder):
    return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []

def app_for(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');store.create('Кампания',Hero(name='Герой'))
    return store

def test_two_downloads_leave_no_temporary_copy_and_no_library_backup(tmp_path):
    store=app_for(tmp_path)
    with TestClient(create_app(store.path,MockLLM())) as client:
        before=copies(tmp_path/'backups')
        for _ in range(2):
            response=client.get('/api/data/export')
            assert response.status_code==200,response.text
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert 'solo.sqlite3' in archive.namelist() and archive.testzip() is None
        assert copies(tmp_path/'backups')==before==[],copies(tmp_path/'backups')
        assert copies(tmp_path/'.exports')==[],copies(tmp_path/'.exports')
        # A download is not a copy a person asked for: it never enters the copy list.
        assert client.get('/api/data/backups').json()['backups']==[]

def test_copies_beyond_the_quota_go_oldest_first(tmp_path):
    store=app_for(tmp_path)
    made=[]
    for _ in range(4):
        made.append(backup(tmp_path,'manual').name)
        time.sleep(0.01)
    removed=prune_backups(tmp_path,keep=3)
    left=copies(tmp_path/'backups')
    assert len(left)==3 and made[0] in removed and made[0] not in left,left
    assert sorted(left)==sorted(made[1:]),left
    assert (tmp_path/'solo.sqlite3').is_file()

def test_quota_never_drops_the_copy_a_restore_points_at(tmp_path):
    store=app_for(tmp_path)
    oldest=backup(tmp_path,'manual')
    for _ in range(3):
        time.sleep(0.01);backup(tmp_path,'manual')
    # plan_restore writes this plan; the quota has to honour it without asking again.
    import json
    (tmp_path/'pending-restore.json').write_text(json.dumps({'backup':oldest.name,'safety':'x'}),encoding='utf-8')
    prune_backups(tmp_path,keep=2)
    left=copies(tmp_path/'backups')
    assert oldest.name in left and len(left)==3,left

def test_oversized_profile_is_refused_before_any_copy_is_written(tmp_path,monkeypatch):
    store=app_for(tmp_path)
    monkeypatch.setattr('solo.data_management.MAX_EXPORT_BYTES',1)
    with TestClient(create_app(store.path,MockLLM())) as client:
        response=client.get('/api/data/export')
        assert response.status_code==409 and 'лимита скачивания' in response.json()['detail'],response.text
    assert copies(tmp_path/'.exports')==[] and copies(tmp_path/'backups')==[]

def test_preview_over_its_memory_budget_is_killed_and_the_next_preview_works(tmp_path,monkeypatch):
    library=library_with_page(tmp_path)
    jobs=ImportJobs(library)
    try:
        monkeypatch.setattr('solo.adventure_library.MAX_RENDER_RSS',1024*1024)
        with pytest.raises(ValueError) as error:
            jobs.render('material-1',600)
        assert 'лимит' in str(error.value),error.value
        # The child is gone and it left no half-drawn file behind.
        assert not any(library.files.glob('*-w600.png')),list(library.files.iterdir())
        monkeypatch.setattr('solo.adventure_library.MAX_RENDER_RSS',768*1024*1024)
        path=jobs.render('material-1',600)
        assert path.is_file()
        with Image.open(path) as image:assert max(image.size)==600,image.size
    finally:jobs.close()

def test_busy_preview_queue_refuses_instead_of_starting_a_third_child(tmp_path,monkeypatch):
    library=library_with_page(tmp_path)
    monkeypatch.setattr('solo.adventure_library.MAX_RENDERS',1)
    monkeypatch.setattr('solo.adventure_library.RENDER_WAIT',0.05)
    jobs=ImportJobs(library)
    try:
        assert jobs.render_slots.acquire(timeout=1)
        with pytest.raises(ValueError) as error:
            jobs.render('material-1',600)
        assert 'Очередь превью' in str(error.value),error.value
    finally:
        jobs.close()
