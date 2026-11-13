import copy,io,json
import pytest
from pypdf import PdfWriter
from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.adventure_engine import AdventureService
from solo.storage import Store
from solo.contracts import Hero
from solo.rulesets import BUILTINS

def book(library,tmp_path,title,pages=3):
    writer=PdfWriter()
    for _ in range(pages):writer.add_blank_page(200,300)
    writer.add_metadata({'/Title':title});path=tmp_path/(title+'.pdf');writer.write(path)
    bundle=library.create(title,{'edition':'2024'});job=library.enqueue(bundle['id'],path,path.name,'book');run(tmp_path,job['id']);return bundle,job['document']

def definition(id,document,**extras):
    return dict(id=id,title=id,ruleset='dnd2024',ruleset_version=1,source_ranges=[dict(document=document,first_page=1,last_page=3)],**extras)

def test_one_pdf_contains_multiple_reviewed_modules_without_forced_order(tmp_path):
    library=AdventureLibrary(tmp_path);bundle,doc=book(library,tmp_path,'Collection')
    entries=[{'id':'DDEX2-1','title':'First','first_page':1,'last_page':1,'ruleset':'dnd2024'},{'id':'DDEX2-10','title':'Second','first_page':2,'last_page':3,'ruleset':'dnd2024'}]
    modules=library.split_collection(bundle['id'],doc,entries,True);assert len(modules)==2 and not any(m['ready'] for m in modules)
    with pytest.raises(ValueError):library.split_collection(bundle['id'],doc,[*entries,{'id':'bad','title':'Bad','first_page':2,'last_page':2}],True)
    library.save_definition(bundle['id'],definition('DDEX2-10',doc),True)
    assert library.definition(bundle['id'],'DDEX2-10').reviewed and library.definition(bundle['id'],'DDEX2-1').reviewed is False

def test_continuation_preserves_hero_inventory_world_and_confirmed_consequences(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');library=AdventureLibrary(tmp_path);one,doc1=book(library,tmp_path,'One');two,doc2=book(library,tmp_path,'Two')
    library.save_definition(one['id'],definition('one',doc1,finish_conditions=[{'path':'adventure.flags.done','value':True}]),True)
    library.save_definition(two['id'],definition('two',doc2,continuation_of=['one']),True)
    campaign=store.create('Continuity',Hero(name='Лира'),BUILTINS['dnd2024']);service=AdventureService(store,library);service.attach(campaign['id'],one['id'],'attach',True)
    def finish(state):state['adventure']['flags'].update(done=True,captain_alive=False);state['facts'].append('Confirmed choice');return [{'type':'scripted','kind':'source_event','text':'Confirmed'}]
    store.record_scenario(campaign['id'],'finish','Confirmed choice',finish)
    before=copy.deepcopy(store.campaign(campaign['id'])['state']);result=service.continue_module(campaign['id'],two['id'],'two','next',True)
    after=store.campaign(campaign['id'])['state']
    for key in ('hero','locations','facts','economy','geography'):
        if key in before:assert after[key]==before[key]
    assert after['adventure']['flags']['captain_alive'] is False and 'one' in after['adventure']['completed_modules']
    assert after['adventure']['volumes']['one']['flags']['done'] is True
    assert service.continue_module(campaign['id'],two['id'],'two','next',True)['id']==result['id']
    store.undo(campaign['id']);assert store.campaign(campaign['id'])['state']['adventure']['module']=='one'

def test_second_book_standalone_requires_source_option_and_does_not_invent_first_history(tmp_path):
    store=Store(tmp_path/'solo.sqlite3');library=AdventureLibrary(tmp_path);bundle,doc=book(library,tmp_path,'Sequel')
    library.save_definition(bundle['id'],definition('two',doc,continuation_of=['one'],alternate_start=True,alternate_flags={'independent_start':True}),True)
    campaign=store.create('Independent',Hero(name='Лира'),BUILTINS['dnd2024']);service=AdventureService(store,library)
    with pytest.raises(ValueError):service.continue_module(campaign['id'],bundle['id'],'two','blocked',True)
    service.continue_module(campaign['id'],bundle['id'],'two','start',True,standalone=True)
    data=store.campaign(campaign['id'])['state']['adventure'];assert not data['completed_modules'] and data['flags']['independent_start'] is True and not data['volumes']

def test_source_edition_is_recorded_separately_and_out_of_book_spans_are_rejected(tmp_path):
    library=AdventureLibrary(tmp_path);bundle,doc=book(library,tmp_path,'2014book');library.update_metadata(bundle['id'],'Book',{'edition':'2014'})
    # A 2014 book on 2024 mechanics is the normal case, not a fault: the book's own
    # edition is recorded beside the mechanics preset instead of refusing the module.
    saved=library.save_definition(bundle['id'],definition('mixed',doc),True)
    assert saved.ruleset=='dnd2024' and saved.source_ruleset=='dnd2014'
    # A module that claims a different source edition than the book carries is a real
    # contradiction, so it is still refused.
    body=definition('wrong',doc);body['source_ruleset']='dnd2024'
    with pytest.raises(ValueError):library.save_definition(bundle['id'],body,True)
    body=definition('range',doc);body['source_ranges'][0]['last_page']=4
    with pytest.raises(ValueError):library.save_definition(bundle['id'],body,True)
