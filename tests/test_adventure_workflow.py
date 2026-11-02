import io,json
from PIL import Image
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.preparation import Draft


def test_selected_collection_module_survives_draft_resume_and_campaign_launch(tmp_path):
    from test_adventure_continuity import book
    app=create_app(tmp_path/'solo.sqlite3');library=app.state.adventures
    bundle,document=book(library,tmp_path,'Collection')
    entries=[dict(id='one',title='First',first_page=1,last_page=1,ruleset='dnd2024'),
             dict(id='two',title='Second',first_page=2,last_page=3,ruleset='dnd2024')]
    library.split_collection(bundle['id'],document,entries,True)
    for entry in entries:
        library.save_definition(bundle['id'],dict(id=entry['id'],title=entry['title'],ruleset='dnd2024',ruleset_version=1,
            source_ranges=[dict(document=document,first_page=entry['first_page'],last_page=entry['last_page'])]),True)
    with TestClient(app) as client:
        draft=client.post('/api/preparation/drafts').json();url='/api/preparation/drafts/'+draft['id']
        body=Draft.model_validate(draft['body']);body.name='Second';body.adventure_bundle=bundle['id'];body.adventure_module='two'
        body.source_kind='ready';body.ruleset='dnd2024';body.ruleset_version=1;body.hero=Hero(name='Лира').model_dump()
        body.opening='Открытый старт второго модуля.';body.prepared=True;body.background_confirmed=True
        client.put(url,json=dict(body=body.model_dump(),revision=draft['revision'])).raise_for_status()
        resumed=client.get(url).json();assert resumed['body']['adventure_module']=='two'
        resumed['body']['step']=7
        client.put(url,json=dict(body=resumed['body'],revision=resumed['revision'])).raise_for_status()
        result=client.post(url+'/launch',json=dict(confirm=True));result.raise_for_status()
        campaign=result.json();assert app.state.store.campaign(campaign['id'])['state']['adventure']['module']=='two'
        assert client.get(url).json()['body']['adventure_module']=='two'
        assert client.post(url+'/launch',json=dict(confirm=True)).json()['id']==campaign['id']

def test_prepared_library_hero_text_clock_handout_undo_and_no_spoilers(tmp_path):
    app=create_app(tmp_path/'solo.sqlite3');library=app.state.adventures;prep=app.state.preparation
    bundle=library.create('Synthetic module',{'edition':'2024'});raw=tmp_path/'synthetic.png';Image.new('RGB',(300,200),'white').save(raw);job=library.enqueue(bundle['id'],raw,'СЕКРЕТНЫЙ_ФАЙЛ.png','handouts');run(tmp_path,job['id'])
    material=library.review(bundle['id'])['materials'][0];library.set_material(material['id'],scope='character',name='Письмо',confirmed=True,sanitized=True,public_text='Идите к старому мосту.')
    document=material['document'];source={'document':document,'review':'confirmed'}
    definition=dict(version=1,id='letter-module',title='Synthetic module',ruleset='dnd2024',ruleset_version=1,media=[material['id']],events=[dict(id='find',category='timed',after_minutes=10,source=source,effects=[dict(kind='handout',target=material['id'])])])
    library.save_definition(bundle['id'],definition,True)
    draft=prep.create();body=Draft.model_validate(draft['body']);body.adventure_bundle=bundle['id'];body.source_kind='ready';body.prepared=True;body.background_confirmed=True;body.hero=Hero(name='Лира').model_dump();body.ruleset='dnd2024';body.opening='Перед героем лесная дорога.';draft=prep.save(draft['id'],body,draft['revision']);campaign=prep.launch(draft['id'],True)
    with TestClient(app) as client:
        cid=campaign['id'];url='/api/campaigns/'+cid+'/adventure/materials/'+material['id']
        assert client.get(url).status_code==404
        result=client.post('/api/campaigns/'+cid+'/play',json={'request_id':'wait','text':'Жду десять минут.'});assert result.status_code==200
        assert client.get(url).status_code==200
        public=client.get('/api/campaigns/'+cid).json();assert public['state']['adventure']['materials'][0]['public_text']=='Идите к старому мосту.'
        assert 'СЕКРЕТНЫЙ_ФАЙЛ' not in json.dumps(public,ensure_ascii=False) and 'definition' not in public['state']['adventure']
        repeated=client.post('/api/campaigns/'+cid+'/play',json={'request_id':'wait','text':'Жду десять минут.'});assert repeated.json()['event']['id']==result.json()['event']['id']
        assert client.get('/api/adventure/review/campaigns/'+cid+'/journal').status_code==403
        client.post('/api/campaigns/'+cid+'/undo');assert client.get(url).status_code==404
        assert prep.launch(draft['id'],True)['id']==cid
