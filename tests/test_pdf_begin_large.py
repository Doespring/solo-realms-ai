"""A book above the draft limit is read by the page library, not refused by it.

The dialog «Начать приключение» accepts a file the draft database will not hold:
above 8 МБ the file is handed to the page library, which streams it in chunks and
reads it in its own worker process. These tests pin both branches, the shape of the
answer, and the page ranges the import proposes from the headings it really found.
"""
import io,time

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject

from solo.adventure_library import AUTO_SPLIT_KEY,AdventureLibrary,chapter_starts,module_spans
from solo.adventure_worker import run
from solo.app import create_app
from solo.preparation import MAX_SOURCE,Preparation
from solo.storage import Store

SCENE='''## Старт: Перекрёсток
Перед вами лесная дорога. У столба ждёт проводница с фонарём.
## Локация: Убежище
За мостом стоит старое убежище.
'''

def chapter_pdf(chapters=4,pages_per_chapter=3,padding=0):
    """A real PDF whose printed header names its chapter, as a book does.

    `padding` writes one unreferenced stream object: pypdf keeps it in the file and
    the reader ignores it, so the file crosses the 8 МБ limit while every page stays
    a real, extractable page.
    """
    writer=PdfWriter()
    for chapter in range(1,chapters+1):
        for _ in range(pages_per_chapter):
            page=writer.add_blank_page(612,792)
            font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
            page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
            stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 30 750 Td (Chapter %d. The Trial of Stone) Tj ET'%chapter).encode('ascii'))
            page[NameObject('/Contents')]=writer._add_object(stream)
    if padding:
        filler=DecodedStreamObject();filler.set_data(b'%'+b'x'*padding);writer._add_object(filler)
    output=io.BytesIO();writer.write(output);return output.getvalue()

def wait_for(client,job,timeout=180):
    deadline=time.monotonic()+timeout;data={}
    while time.monotonic()<deadline:
        data=client.get('/api/adventure/jobs/'+job).json()
        if data['status'] in ('complete','failed','cancelled'):return data
        time.sleep(.2)
    raise AssertionError('импорт не завершился за '+str(timeout)+' с: '+repr(data))

def test_small_source_keeps_the_previous_answer_shape(tmp_path):
    store=Store(tmp_path/'solo.sqlite3')
    with TestClient(create_app(store.path)) as client:
        draft=client.post('/api/preparation/drafts',json={}).json()
        response=client.post('/api/preparation/drafts/'+draft['id']+'/sources',files={'file':('сцена.md',SCENE.encode('utf-8'),'text/markdown')})
        assert response.status_code==200,response.text
        body=response.json()
        assert 'path' not in body and 'sources' in body
        assert body['sources'][0]['quality']['status']=='usable'

def test_book_above_the_limit_is_read_by_the_library_and_split_into_modules(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');raw=chapter_pdf(4,3,9*1024*1024)
    assert len(raw)>MAX_SOURCE
    with TestClient(create_app(store.path)) as client:
        draft=client.post('/api/preparation/drafts',json={}).json()
        response=client.post('/api/preparation/drafts/'+draft['id']+'/sources',files={'file':('Руководство.pdf',raw,'application/pdf')})
        assert response.status_code==200,response.text
        body=response.json()
        assert set(body)=={'path','job','bundle'} and body['path']=='library'
        assert 'превышает' not in response.text
        draft=client.get('/api/preparation/drafts/'+draft['id']).json()
        assert draft['body']['adventure_bundle']==body['bundle']
        job=wait_for(client,body['job'])
        assert job['status']=='complete',job
        assert (job['done'],job['total'])==(12,12)
        assert (job['chapters'],job['modules'])==(4,4)
        modules=client.get('/api/adventure/library/'+body['bundle']+'/modules').json()
        assert len(modules)==4 and all(module['ready'] for module in modules)
        assert [module['source_pages'][0] for module in modules]==[[1,3],[4,6],[7,9],[10,12]]
        book=next(b for b in client.get('/api/adventure/library').json() if b['id']==body['bundle'])
        assert book['import_complete'] is True

class _Jobs:
    """The service only hands the id to the job queue; the import itself is not this test."""
    def submit(self,id):self.submitted=id

def test_second_book_reuses_the_bundle_the_draft_already_points_at(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');preparation=Preparation(tmp_path,store)
    library=AdventureLibrary(tmp_path);draft=preparation.create()
    for name in ('первая.md','вторая.md'):
        path=tmp_path/name;path.write_text(SCENE,encoding='utf-8')
        answer=preparation.add_library_source(draft['id'],name,path,library,_Jobs())
        assert answer['path']=='library' and answer['bundle']
        draft=preparation.get(draft['id'])
        assert draft['body']['adventure_bundle']==answer['bundle']

def test_auto_split_is_idempotent_and_a_heading_free_text_stays_one_module(tmp_path):
    library=AdventureLibrary(tmp_path)
    book=library.create('Книга',{AUTO_SPLIT_KEY:True})
    path=tmp_path/'book.pdf';path.write_bytes(chapter_pdf(4,3))
    job=library.enqueue(book['id'],path,path.name,'book');run(library.data,job['id'])
    modules=library.modules(book['id']);assert len(modules)==4 and all(module['ready'] for module in modules)
    summary=library.auto_split(book['id'],job['document'])
    assert len(summary['modules'])==4 and len(library.modules(book['id']))==4
    plain=library.create('Проза',{AUTO_SPLIT_KEY:True})
    source=tmp_path/'plain.txt';source.write_text('Просто проза без заголовков и без разметки. '*40,encoding='utf-8')
    job=library.enqueue(plain['id'],source,source.name,'book');run(library.data,job['id'])
    fallback=library.modules(plain['id'])
    assert len(fallback)==1 and fallback[0]['source_pages']==[[1,1]]
    assert fallback[0]['ready'] is False and 'Границы' in fallback[0]['notes'][0]

def test_a_hand_made_bundle_keeps_the_reviewed_split_path(tmp_path):
    library=AdventureLibrary(tmp_path);bundle=library.create('Сборник',{'edition':'2024'})
    path=tmp_path/'collection.pdf';path.write_bytes(chapter_pdf(4,3))
    job=library.enqueue(bundle['id'],path,path.name,'book');run(library.data,job['id'])
    assert library.modules(bundle['id'])==[]

def test_boundaries_come_from_the_number_in_the_running_header():
    assert chapter_starts([(1,'chapter','Глава 1. Начало'),(2,'chapter','Глава 1 | Начало'),(5,'chapter','Глава 2. Дальше')])==[(1,'Глава 1. Начало'),(5,'Глава 2. Дальше')]
    assert module_spans([(1,'пусто'),(2,'глава'),(6,'глава')],8)==[(1,5,'пусто'),(6,8,'глава')]
    assert module_spans([],5)==[] and module_spans([(1,'a')],1)==[(1,1,'a')]
