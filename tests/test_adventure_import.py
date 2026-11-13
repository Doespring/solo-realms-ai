import io,json,zipfile,sqlite3
from pathlib import Path
import pytest
from PIL import Image
from pypdf import PdfWriter
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run,svg_safe
from solo.user_data import backup
from solo.storage import Store

def pdf_bytes(pages=1,title=''):
    writer=PdfWriter()
    writer.add_metadata({'/Title':title})
    for _ in range(pages):writer.add_blank_page(200,300)
    result=io.BytesIO();writer.write(result);return result.getvalue()

def imported(tmp_path,name='карта.pdf',raw=None,purpose='maps'):
    library=AdventureLibrary(tmp_path);bundle=library.create('Тестовый комплект')
    path=tmp_path/'upload.bin';path.write_bytes(raw or pdf_bytes());job=library.enqueue(bundle['id'],path,name,purpose);run(tmp_path,job['id'])
    return library,bundle,job

def test_pdf_maps_are_really_rendered_and_repeat_preserves_review(tmp_path):
    library,bundle,job=imported(tmp_path,raw=pdf_bytes(2))
    media=library.review(bundle['id'])['materials'];assert len(media)==2
    for material in media:
        preview=library.files/(material['document']+'-p'+str(material['page'])+'-w480.png')
        with Image.open(preview) as image:assert image.width>200 and image.height==480
        assert material['scope']=='gm'
    library.set_material(media[0]['id'],scope='player',name='Открытая карта',confirmed=True,sanitized=True)
    old=library.enqueue(bundle['id'],tmp_path/'upload.bin','карта.pdf','maps');assert old['id']==job['id']
    run(tmp_path,job['id']);assert library.material(media[0]['id'])['scope']=='player'
    assert library.get(bundle['id'])['status']=='documents_imported'

def test_zip_contains_39_pdfs_with_unicode_and_actual_page_previews(tmp_path):
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for n in range(39):z.writestr(f'каталог/карта-{n}.pdf',pdf_bytes(title=str(n)))
    library,bundle,job=imported(tmp_path,name='maps.zip',raw=archive.getvalue())
    review=library.review(bundle['id']);assert len(review['materials'])==39
    assert len([d for d in review['documents'] if d['format']=='.pdf'])==39
    with library.db() as db:assert db.execute('SELECT count(*) FROM bundle_files WHERE parent!=""').fetchone()[0]==39

@pytest.mark.parametrize('filename',['../escape.pdf','C:/escape.pdf','safe/program.exe','safe/script.py'])
def test_archive_rejects_traversal_and_executables_without_running_them(tmp_path,filename):
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:z.writestr(filename,pdf_bytes())
    library=AdventureLibrary(tmp_path);bundle=library.create('Bad ZIP');path=tmp_path/'archive.zip';path.write_bytes(archive.getvalue());job=library.enqueue(bundle['id'],path,'bad.zip','maps')
    with pytest.raises(ValueError):run(tmp_path,job['id'])
    assert library.job(job['id'])['status']=='failed' and not (tmp_path.parent/'escape.pdf').exists()

def test_archive_expansion_bomb_is_rejected_before_import(tmp_path):
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('bomb.txt',b'x'*2000000)
    library=AdventureLibrary(tmp_path);bundle=library.create('Bomb');path=tmp_path/'archive.zip';path.write_bytes(archive.getvalue());job=library.enqueue(bundle['id'],path,'bad.zip','maps')
    with pytest.raises(ValueError):run(tmp_path,job['id'])
    assert not library.review(bundle['id'])['materials']

def test_review_requires_separate_scope_and_player_does_not_receive_hidden_media(tmp_path):
    library,bundle,job=imported(tmp_path,name='СЕКРЕТНАЯ_КОМНАТА.pdf')
    material=library.review(bundle['id'])['materials'][0]
    with TestClient(create_app(tmp_path/'solo.sqlite3')) as client:
        public=client.get('/api/adventure/library').text;assert 'СЕКРЕТНАЯ_КОМНАТА' not in public
        assert client.get('/api/adventure/review/materials/'+material['id']).status_code==403
        assert client.post('/api/adventure/review/session',json={'confirm_spoilers':False}).status_code==409
        assert client.post('/api/adventure/review/session',json={'confirm_spoilers':True}).status_code==200
        assert client.get('/api/adventure/review/library/'+bundle['id']).status_code==200
        client.delete('/api/adventure/review/session')
        assert client.get('/api/adventure/review/library/'+bundle['id']).status_code==403

def test_svg_external_resources_and_code_are_rejected(tmp_path):
    for raw in [b'<svg><script>evil()</script></svg>',b'<svg><image href="http://evil"/></svg>',b'<!DOCTYPE svg><svg/>']:
        with pytest.raises(ValueError):svg_safe(raw)
    assert b'path' in svg_safe(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0L10 10"/></svg>')

def test_profile_backup_contains_private_library_and_original_files(tmp_path):
    Store(tmp_path/'solo.sqlite3');library,bundle,job=imported(tmp_path)
    folder=backup(tmp_path,'test');assert (folder/'adventure.sqlite3').is_file()
    for source in library.files.glob('*.bin'):assert (folder/'adventure-files'/source.name).read_bytes()==source.read_bytes()
