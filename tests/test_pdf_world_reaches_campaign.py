"""The book a player reads before the hero step is still there when the game starts.

«Начать приключение» stores what it read in a preparation draft, the hero editor
creates the campaign, and both read that same draft. These tests pin the chain end
to end: the module the import cut is stored on the draft, survives the hero step,
and reaches the campaign as its opening page — or the campaign says plainly that
it starts without the book instead of pretending an empty world is the book.
"""
import io,time,uuid

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject

from solo.adventure_library import AUTO_SPLIT_KEY,AdventureLibrary
from solo.app import create_app
from solo.preparation import MAX_SOURCE,Draft
from solo.preview_mock import PreviewMock

PROSE='Просто проза без заголовков, глав и разметки: так выглядит текст, в котором '\
      'границу главы найти нечем. '


def chapter_pdf(chapters=4,pages_per_chapter=3,padding=0):
    """A real PDF whose printed header names its chapter, as a book does."""
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


def start(client,raw,name='Руководство.pdf'):
    """The dialog path: a draft, a file above the limit, and the import job."""
    draft=client.post('/api/preparation/drafts',json={}).json()
    response=client.post('/api/preparation/drafts/'+draft['id']+'/sources',files={'file':(name,raw,'application/pdf')})
    assert response.status_code==200,response.text
    body=response.json();assert body.get('path')=='library',body
    job=wait_for(client,body['job']);assert job['status']=='complete',job
    return draft['id'],body['bundle'],job


def campaign(client,draft_id,build,request_id):
    return client.post('/api/prototype/campaigns',json=dict(name='Лира',request_id=request_id,build=build,
        adventure='sandbox',preparation_draft=draft_id,encounter_monsters=[]))


def default_build(client):
    # The wizard's own legal starting sheet, so the test does not hand-assemble one.
    return client.get('/api/prototype/options').json()['default_build']


def app_for(tmp_path):
    return create_app(tmp_path/'solo.sqlite3',PreviewMock(),experimental=True,offline_mock=True)


def test_the_module_lands_on_the_draft_and_the_hero_step_still_sees_it(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        draft_id,bundle,job=start(client,chapter_pdf(4,3,9*1024*1024))
        assert (job['chapters'],job['modules'])==(4,4)
        world=client.post('/api/preparation/drafts/'+draft_id+'/world',json={})
        assert world.status_code==200,world.text
        world=world.json()
        assert world['bound'] is True and world['modules']==4
        assert world['module'].endswith('-01') and world['pages']==[[1,3]]
        # The title is the book's own header, not a page range bolted onto it:
        # the pages travel beside it in `pages`, and a chapter that printed
        # translator credits on its first page must not be named after them.
        assert world['title']=='Chapter 1. The Trial of Stone'
        # The hero step reads the same server-side draft, so nothing is carried in
        # the browser between the upload and the editor.
        reopened=client.get('/api/preparation/drafts/'+draft_id).json()
        assert reopened['body']['adventure_bundle']==bundle
        assert reopened['body']['adventure_module']==world['module']
        again=client.post('/api/preparation/drafts/'+draft_id+'/world',json={}).json()
        assert again['module']==world['module'] and again['pages']==world['pages']
        assert client.get('/api/preparation/drafts/'+draft_id).json()['revision']==reopened['revision']


def test_the_campaign_from_the_hero_editor_starts_on_the_page_the_module_starts_at(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        draft_id,bundle,job=start(client,chapter_pdf(4,3,9*1024*1024))
        world=client.post('/api/preparation/drafts/'+draft_id+'/world',json={}).json()
        created=campaign(client,draft_id,default_build(client),'r149-'+uuid.uuid4().hex[:8])
        assert created.status_code==200,created.text
        created=created.json()
        assert created['book']['module']==world['module'] and created['book']['pages']==[[1,3]]
        assert created['notice'].startswith('Книга привязана') and 'стр. 1–3' in created['notice']
        state=client.app.state.store.campaign(created['id'])['state']
        library=state['preparation']['library']
        assert (library['bundle'],library['module'],library['page'])==(bundle,world['module'],1)
        assert library['pages']==[[1,3]]
        # The opening scene is the page the module starts at, not an empty world.
        # The place is named by what the chapter printed: its own title without the
        # chapter number, so the hero stands in «The Trial of Stone» rather than in
        # «Chapter 1. The Trial of Stone» or in «стр. 1–3».
        assert (state['location'],world['title'])==('The Trial of Stone','Chapter 1. The Trial of Stone')
        first_page=client.app.state.adventures.page_text(bundle,library['document'],1)
        assert first_page.strip()
        # A04/F07: страница книги хранится отдельно, а в описание места идёт только
        # открытая часть. Заголовок «Chapter 1. …» — подпись страницы, а не сцена,
        # и в описании места ему делать нечего.
        assert library['source_text']==first_page.strip()[:2000]
        assert 'Chapter 1' not in state['locations'][0]['description']
        assert state['locations'][0]['description'].strip()
        # The first event of the campaign cites that page.
        history=client.get('/api/campaigns/'+created['id']).json()['history']
        setup=next(row for row in history if row['request_id']=='prepared-world')
        assert setup['results'][0]['source']['page']==1
        assert setup['results'][0]['source']['pages']==[[1,3]]
        assert setup['results'][0]['source']['module']==world['module']
        # A journal step holds at most 800 characters; the page itself may be
        # longer and then stays in the place description.
        assert 0<len(setup['results'][0]['text'])<=800
        assert len(state['locations'][0]['description'])<=2000
        # The draft now belongs to this campaign and cannot start a second one.
        assert client.get('/api/preparation/drafts/'+draft_id).json()['campaign']==created['id']


def test_a_book_without_chapter_boundaries_starts_without_it_and_says_so(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        # Above the draft limit, below the 12 МБ text limit: a book long enough to
        # need the library, and still without a single chapter boundary in it.
        prose=(PROSE*((9*1024*1024)//len(PROSE.encode('utf-8'))+1)).encode('utf-8')
        assert MAX_SOURCE<len(prose)<12*1024*1024
        draft_id,bundle,job=start(client,prose,'Проза.txt')
        assert job['modules']==1
        world=client.post('/api/preparation/drafts/'+draft_id+'/world',json={}).json()
        assert world['bound'] is False and 'без книги' in world['reasons'][0]
        assert client.get('/api/preparation/drafts/'+draft_id).json()['body']['adventure_module']==''
        created=campaign(client,draft_id,default_build(client),'r149-plain-'+uuid.uuid4().hex[:8])
        assert created.status_code==200,created.text
        created=created.json()
        assert 'book' not in created and 'без книги' in created['notice']
        state=client.app.state.store.campaign(created['id'])['state']
        assert 'library' not in (state.get('preparation') or {})
        assert not any(row.get('request_id')=='prepared-world' for row in client.get('/api/campaigns/'+created['id']).json()['history'])


def test_a_second_start_with_the_same_book_is_refused_before_a_campaign_exists(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        draft_id,bundle,job=start(client,chapter_pdf(4,3,9*1024*1024))
        client.post('/api/preparation/drafts/'+draft_id+'/world',json={})
        first=campaign(client,draft_id,default_build(client),'r149-first-'+uuid.uuid4().hex[:8])
        assert first.status_code==200,first.text
        second=campaign(client,draft_id,default_build(client),'r149-second-'+uuid.uuid4().hex[:8])
        assert second.status_code==409,second.text
        assert 'уже начал кампанию' in second.json()['detail']
        campaigns=client.app.state.store.list_campaigns()
        assert len(campaigns)==1,campaigns


def test_a_retry_of_the_same_start_returns_the_same_campaign(tmp_path):
    # A lost answer is retried with the same request_id; that must not be read as
    # a second start of an already started draft.
    with TestClient(app_for(tmp_path)) as client:
        draft_id,bundle,job=start(client,chapter_pdf(4,3,9*1024*1024))
        client.post('/api/preparation/drafts/'+draft_id+'/world',json={})
        build=default_build(client);request_id='r149-retry-'+uuid.uuid4().hex[:8]
        first=campaign(client,draft_id,build,request_id)
        assert first.status_code==200,first.text
        repeat=campaign(client,draft_id,build,request_id)
        assert repeat.status_code==200,repeat.text
        assert repeat.json()['id']==first.json()['id']
        assert len(client.app.state.store.list_campaigns())==1


def test_a_module_without_pages_cannot_claim_a_scene(tmp_path):
    library=AdventureLibrary(tmp_path);bundle=library.create('Книга',{AUTO_SPLIT_KEY:True})
    with library.db() as db:
        body=dict(id='auto-00000000-01',title='Глава без страниц',ruleset='dnd2024',ruleset_version=1,
                  source_ranges=[],reviewed=True,unsupported=[])
        db.execute('INSERT INTO adventure_modules VALUES(?,?,?,1)',(bundle['id'],body['id'],__import__('json').dumps(body)))
    with TestClient(app_for(tmp_path)) as client:
        preparation=client.app.state.preparation
        draft=preparation.create();saved=Draft.model_validate(draft['body'])
        saved.adventure_bundle=bundle['id']
        preparation.save(draft['id'],saved,draft['revision'])
        world=client.post('/api/preparation/drafts/'+draft['id']+'/world',json={}).json()
        assert world['bound'] is False and 'не называет' in world['reasons'][0],world
        created=campaign(client,draft['id'],default_build(client),'r149-pageless-'+uuid.uuid4().hex[:8])
        assert created.status_code==200,created.text
        assert 'book' not in created.json() and 'без книги' in created.json()['notice']
        assert 'library' not in (client.app.state.store.campaign(created.json()['id'])['state'].get('preparation') or {})


def test_a_hero_built_without_a_book_gets_no_book_attached(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        created=campaign(client,'',default_build(client),'r149-sandbox-'+uuid.uuid4().hex[:8])
        assert created.status_code==200,created.text
        assert set(created.json())=={'id','name','backend'}
        state=client.app.state.store.campaign(created.json()['id'])['state']
        assert 'preparation' not in state or 'library' not in state['preparation']
