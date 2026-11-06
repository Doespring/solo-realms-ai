import io,json,asyncio
from pathlib import Path
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
from fastapi.testclient import TestClient
from solo.adventure_library import AdventureLibrary
from solo.adventure_worker import run
from solo.pdf_pipeline import PDFPipeline
from solo.adventure_analysis import AnalysisResult,AnalysisRequest,AdventureAnalysis,validate_evidence
from solo.rule_registry import coverage,status
from solo.app import create_app
from solo.llm import MockLLM
from test_ai_tuning1 import setup
from test_cloud import sse
import httpx
from decimal import Decimal

def source(tmp_path):
    writer=PdfWriter();p=writer.add_blank_page(300,200)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    p[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 10 170 Td (Table rewards) Tj 0 -20 Td (1-3 Gold 10) Tj 0 -20 Td (4-6 Silver 20) Tj ET')
    p[NameObject('/Contents')]=writer._add_object(stream);out=io.BytesIO();writer.write(out)
    path=tmp_path/'source.pdf';path.write_bytes(out.getvalue());library=AdventureLibrary(tmp_path/'library');bundle=library.create('Generic test',dict(edition='2024'))
    job=library.enqueue(bundle['id'],path,path.name,'book');run(library.data,job['id']);assert library.job(job['id'])['status']=='complete'
    return library,bundle,job['document']

def entity(**changes):return dict(id='rewards',kind='table',page=1,name='Table rewards',quote='Table rewards\r\n1-3 Gold 10\r\n4-6 Silver 20',cells=[dict(row=0,column=0,text='Gold'),dict(row=0,column=1,text='10')],**changes)

def test_import_builds_offsets_and_geometry_without_copying_text_and_reuses_source(tmp_path):
    lib,bundle,doc=source(tmp_path);index=PDFPipeline(lib);page=index.page(doc,1)
    with lib.db() as db:text=db.execute('SELECT content FROM pages WHERE document=?',(doc,)).fetchone()[0]
    assert page['regions'] and all(r['bounds'] for r in page['regions'])
    assert [text[r['start']:r['end']] for r in page['regions']]==['Table rewards','1-3 Gold 10','4-6 Silver 20']
    assert 'Table rewards' not in json.dumps(page) and page['semantic_status']=='requires_review'
    assert any(h['kind']=='table' for h in page['hints'])
    result=index.index(doc);assert result['cached']==1 and result['indexed']==0
    assert lib.enqueue(bundle['id'],tmp_path/'source.pdf','source.pdf','book')['id']
    assert len(list(lib.files.glob('*.bin')))==1
    with lib.db() as db:assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()

def test_stale_layout_requires_recheck_and_failure_does_not_destroy_source(tmp_path):
    lib,_,doc=source(tmp_path);index=PDFPipeline(lib);blob=lib.blob(doc).read_bytes()
    with lib.db() as db:db.execute('UPDATE pages SET content="changed" WHERE document=?',(doc,))
    with pytest.raises(ValueError,match='устарел'):index.page(doc,1)
    result=index.index(doc);assert result['errors'][0]['page']==1 and result['indexed']==0
    assert index.summary(doc)['stale_pages']==[1] and index.summary(doc)['indexed_pages']==0
    assert lib.blob(doc).read_bytes()==blob

def test_index_retries_only_missing_pages_and_checks_range(tmp_path):
    lib,_,doc=source(tmp_path);index=PDFPipeline(lib)
    with lib.db() as db:db.execute('DELETE FROM pdf_layout WHERE document=?',(doc,))
    result=index.index(doc,[1]);assert result['indexed']==1 and not result['errors']
    with pytest.raises(ValueError):index.index(doc,[2])

def test_entities_cells_and_links_remain_quarantined(tmp_path):
    lib,_,doc=source(tmp_path);index=PDFPipeline(lib)
    with lib.db() as db:text=db.execute('SELECT content FROM pages WHERE document=?',(doc,)).fetchone()[0]
    e=entity(links=[dict(relation='requires',target='later')]);e['quote']=text
    result=AnalysisResult(suggestions=[],problems=[],entities=[e]);validate_evidence(result,[dict(page=1,text=text)],layouts={1:index.page(doc,1)})
    assert result.entities[0].links[0].status=='unresolved' and not result.entities[0].executable
    assert result.entities[0].source_refs==['p1.render']
    for field,value in [('cells',[dict(row=0,column=0,text='invented')]),('source_refs',['p999.render']),('name','Invented')]:
        bad=AnalysisResult(suggestions=[],problems=[],entities=[{**e,field:value}])
        with pytest.raises(ValueError,match='entities.0'):validate_evidence(bad,[dict(page=1,text=text)],layouts={1:index.page(doc,1)})
    wrong=AnalysisResult(suggestions=[],problems=[],entities=[{**e,'source_refs':[index.page(doc,1)['regions'][1]['id']]}])
    with pytest.raises(ValueError,match='source_refs'):validate_evidence(wrong,[dict(page=1,text=text)],layouts={1:index.page(doc,1)})
    with pytest.raises(ValueError):AnalysisResult(suggestions=[],problems=[],entities=[dict(e,code='import os')])

def test_visual_claims_need_actual_pixels_and_do_not_prove_rules():
    e=dict(id='map',kind='map',page=1,name='uncertain',source='image')
    value=AnalysisResult(suggestions=[],problems=[],entities=[e])
    with pytest.raises(ValueError,match='изображение'):validate_evidence(value,[dict(page=1,text='')])
    validate_evidence(value,[dict(page=1,text='')],image_pages=[1]);assert not value.entities[0].executable

def test_gemini_local_layout_then_qwen_structure_mocked_and_consent_gated(tmp_path):
    lib,bundle,doc=source(tmp_path);requests=[]
    def respond(r):
        requests.append(json.loads(r.content));body=dict(suggestions=[],problems=[],classifications=[dict(page=1,kind='map',source='image',needs_visual_review=True,transcription='uncertain visual candidate')]) if len(requests)==1 else dict(suggestions=[],problems=[],entities=[dict(id='table',kind='table',name='Table rewards',page=1,quote='Table rewards')])
        return sse([{'choices':[{'delta':{'content':json.dumps(body)}}]}])
    llm,p=setup(tmp_path,respond);p.enabled=True;p.pdf_analysis_enabled=True;p.pdf_analyst.image_per_million=Decimal(1);service=AdventureAnalysis(lib,llm)
    async def execute():
        visual=AnalysisRequest(document=doc,pages=[1],role='gemini',include_images=True,problem='Classify visible table or map',request_id='v')
        with pytest.raises(ValueError,match='Подтвердите'):await service.analyze(bundle['id'],visual)
        assert not requests
        visual.consent_to_send=True;result=await service.analyze(bundle['id'],visual);assert result['status']=='requires_review'
        qwen=AnalysisRequest(document=doc,pages=[1],consent_to_send=True,request_id='q')
        out=await service.analyze(bundle['id'],qwen);assert not out['executable'] and out['identity']['ruleset']=='dnd2024'
        user=json.loads(requests[1]['messages'][1]['content']);assert user['visual_context'][0]['status']=='requires_review'
        assert 'schema' not in user and 'catalog' not in user
        cached=await service.analyze(bundle['id'],qwen);assert cached['cached'] and len(requests)==2
        p.interpreter.temperature+=.01;await service.analyze(bundle['id'],qwen);assert len(requests)==3
        await llm.close()
    asyncio.run(execute())
    with pytest.raises(ValueError):lib.definition(bundle['id'])

def test_registry_is_honest_readonly_and_edition_specific(tmp_path):
    assert len(coverage())==18 and status('checks.ability','dnd2014')=='implemented'
    assert status('magic.casting','dnd2024')=='unsupported' and status('advancement.level_up','dnd2014')=='unsupported'
    assert status('advancement.level_up','dnd2024')=='partial'
    assert status('rest.recovery','dnd2024')=='partial' and status('arbitrary.ai.code','dnd2024')=='unsupported'
    with pytest.raises(ValueError):coverage('dnd2030')
    with TestClient(create_app(tmp_path/'game.sqlite3',MockLLM())) as c:
        result=c.get('/api/rules/coverage?edition=dnd2024');assert result.status_code==200 and not result.json()['candidates_can_execute']
        assert all(r['edition']=='dnd2024' for r in result.json()['rules'])

def test_local_index_api_is_private_free_and_rejects_unrelated_sources(tmp_path):
    lib,bundle,doc=source(tmp_path);llm=MockLLM()
    with TestClient(create_app(lib.data/'game.sqlite3',llm)) as c:
        url='/api/adventure/review/library/'+bundle['id']+'/index'
        assert c.post(url,json=dict(document=doc)).status_code==403
        c.post('/api/adventure/review/session',json=dict(confirm_spoilers=True)).raise_for_status()
        result=c.post(url,json=dict(document=doc));result.raise_for_status();assert result.json()['cached']==1 and result.json()['paid_requests']==0
        assert c.post(url,json=dict(document='f'*64)).status_code==409 and not llm.metrics

def test_fragment_cursor_is_visible_and_untransmitted_text_cannot_be_cited(tmp_path):
    lib=AdventureLibrary(tmp_path/'library');bundle=lib.create('Long page');path=tmp_path/'long.txt';path.write_text('a'*12000+' END',encoding='utf8');job=lib.enqueue(bundle['id'],path,path.name,'book');run(lib.data,job['id']);requests=[]
    def respond(r):
        requests.append(json.loads(r.content));return sse([{'choices':[{'delta':{'content':json.dumps(dict(suggestions=[dict(page=1,kind='gm_note',quote='END',interpretation='Found exact tail')],problems=[]))}}]}])
    llm,p=setup(tmp_path,respond);p.enabled=True
    async def execute():
        service=AdventureAnalysis(lib,llm);body=AnalysisRequest(document=job['document'],pages=[1],consent_to_send=True,request_id='first')
        with pytest.raises(ValueError,match='suggestions.0.quote'):await service.analyze(bundle['id'],body)
        out=await service.analyze(bundle['id'],body.model_copy(update=dict(text_offset=10000,request_id='tail')))
        assert out['coverage'][0]==dict(page=1,start=10000,end=12004,total=12004,next_offset=None)
        first=json.loads(requests[0]['messages'][1]['content']);assert len(first['pages'][0]['text'])==10000 and 'END' not in first['pages'][0]['text']
        await llm.close()
    asyncio.run(execute())

def test_duplicate_json_keys_fail_once_without_poisoning_cache(tmp_path):
    lib,bundle,doc=source(tmp_path);requests=[]
    def respond(r):
        requests.append(r);return sse([{'choices':[{'delta':{'content':'{"suggestions":[],"problems":[],"entities":[],"entities":[]}'}}]}])
    llm,p=setup(tmp_path,respond);p.enabled=True
    async def execute():
        with pytest.raises(ValueError):await AdventureAnalysis(lib,llm).analyze(bundle['id'],AnalysisRequest(document=doc,pages=[1],consent_to_send=True,request_id='duplicate'))
        assert len(requests)==1
        await llm.close()
    asyncio.run(execute())
    with lib.db() as db:assert db.execute('SELECT count(*) FROM analysis_cache').fetchone()[0]==0
