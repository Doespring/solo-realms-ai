import asyncio,io,json,time
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from solo.app import create_app
from solo.source_upload import receive_source
from solo.adventure_library import AdventureLibrary,ImportJobs,digest

def wait_job(client,id):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        value=client.get('/api/adventure/jobs/'+id).json()
        if value['status'] not in ('queued','running'):return value
        time.sleep(.05)
    raise AssertionError(value)

def test_simple_draft_uses_library_persists_text_excludes_without_deleting_source(tmp_path):
    with TestClient(create_app(tmp_path/'solo.sqlite3')) as client:
        draft=client.post('/api/creation/drafts').json();url='/api/creation/drafts/'+draft['id']
        draft=client.put(url,json=dict(description='Мрачная история о севере. Учитывай приложенную книгу.',revision=draft['revision'])).json()
        raw=('Это закрытый исходник. СЕКРЕТ_МАСТЕРА: предатель скрывается в башне.\n'*80).encode()
        response=client.post(url+'/files',files={'file':('book.md',raw)});assert response.status_code==200,response.text
        draft=response.json();source=draft['files'][0];assert wait_job(client,source['job']['id'])['status']=='complete'
        assert 'СЕКРЕТ_МАСТЕРА' not in json.dumps(draft,ensure_ascii=False)
        assert client.get('/api/adventure/review/library/'+client.app.state.creation.owned(draft['id'])['body']['adventure_bundle']).status_code==403
        duplicate=client.post(url+'/files',files={'file':('copy.md',raw)}).json();assert len(duplicate['files'])==1
        path=client.app.state.adventures.blob(source['sha256']);assert digest(path)==source['sha256']
        assert client.delete(url+'/files/'+source['document']).json()['files']==[]
        assert path.exists() and digest(path)==source['sha256'] and not client.app.state.creation.allowed_documents(draft['id'])
        restored=client.post(url+'/files',files={'file':('book.md',raw)}).json();assert len(restored['files'])==1
        assert restored['description'].startswith('Мрачная история')
        assert client.post(url+'/prepare',json={'request_id':'not-configured','ruleset':'dnd2014','consent_to_send':True}).status_code==503
        assert client.get('/api/campaigns').json()==[]
        bundle=client.app.state.creation.owned(draft['id'])['body']['adventure_bundle']
        cloned=client.post('/api/creation/from-library/'+bundle,json={});assert cloned.status_code==200,cloned.text
        clone=cloned.json();assert len(clone['files'])==1 and clone['files'][0]['sha256']==source['sha256']
        assert client.delete('/api/creation/drafts/'+clone['id']+'/files/'+source['document']).status_code==200
        assert len(client.get(url).json()['files'])==1
        stale=client.put(url,json=dict(description='Потеря данных',revision=0));assert stale.status_code==409
    with TestClient(create_app(tmp_path/'solo.sqlite3')) as client:
        assert client.get(url).json()['description'].startswith('Мрачная история')
        assert len(client.get('/api/creation/drafts').json())==2

def test_stream_limit_disconnect_and_failed_upload_cleanup(tmp_path):
    library=AdventureLibrary(tmp_path);bundle=library.create('Fixture')['id']
    jobs=ImportJobs(library)
    class SmallReads(UploadFile):
        async def read(self,size=-1):
            assert size==1024*1024
            return await super().read(size)
    try:
        with patch('solo.source_upload.MAX_FILE',1024):
            # Предел подменён на 1 КиБ: отказ обязан назвать ИМЕННО его (раньше
            # тут стояло литеральное «384 МиБ», не связанное с проверкой), и то,
            # сколько уже принято.
            with pytest.raises(ValueError,match='1,0 КиБ.*2,0 КиБ'):asyncio.run(receive_source(library,jobs,bundle,SmallReads(io.BytesIO(b'a'*2048),filename='book.txt')))
        class Disconnected:
            async def is_disconnected(self):return True
        with pytest.raises(asyncio.CancelledError):asyncio.run(receive_source(library,jobs,bundle,SmallReads(io.BytesIO(b'x'),filename='book.txt'),request=Disconnected()))
        assert not list(library.files.glob('.upload-*'))
        assert library.get(bundle)['documents']==0
    finally:jobs.close()

def test_invalid_format_and_empty_preparation_remain_recoverable(tmp_path):
    with TestClient(create_app(tmp_path/'solo.sqlite3')) as client:
        draft=client.post('/api/creation/drafts').json();url='/api/creation/drafts/'+draft['id']
        assert client.post(url+'/prepare',json={'request_id':'empty'}).status_code==409
        assert client.post(url+'/files',files={'file':('bad.exe',b'bad')}).status_code==409
        assert client.get(url).json()['files']==[]
        assert not list(client.app.state.adventures.files.glob('.upload-*'))
        legacy=client.post('/api/preparation/drafts').json()
        assert client.get('/api/creation/drafts/'+legacy['id']).status_code==409

def test_upload_body_is_bounded_before_multipart_spooling(tmp_path):
    with TestClient(create_app(tmp_path/'solo.sqlite3')) as client:
        draft=client.post('/api/creation/drafts').json();url='/api/creation/drafts/'+draft['id']+'/files'
        assert client.post(url,content=b'x',headers={'content-length':str(500*1048576)}).status_code==413
        with patch('solo.upload_limit.MAX_FILE',4):
            chunks=[b'--bounded\r\nContent-Disposition: form-data; name="file"; filename="large.txt"\r\nContent-Type: text/plain\r\n\r\n',b'x'*1048576,b'x'*1048576,b'\r\n--bounded--\r\n']
            response=client.post(url,content=iter(chunks),headers={'content-type':'multipart/form-data; boundary=bounded'})
            assert response.status_code==413
