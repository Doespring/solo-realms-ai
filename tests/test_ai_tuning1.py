import asyncio,json,copy
import pytest,httpx
from solo.action_validator import IntentPacket,ActionValidator
from solo.prompt_compaction import compact_schema,shared_contracts,character_catalog
from solo.narrative_grounding import verify,fallback,render,CanonViolation,sources
from solo.adventure_analysis import AnalysisResult,validate_evidence
from test_api_gm_hotfix import setup as base_setup
from decimal import Decimal
from test_cloud import sse

def setup(tmp_path,respond):
    llm,p=base_setup(tmp_path,respond)
    for field in ('max_request_cost','session_budget','daily_budget','monthly_budget','preparation_budget'):setattr(p,field,Decimal(10))
    return llm,p

PACKET=dict(packet_version=2,place='Таверна',atmosphere='Тихая таверна.',npc=[dict(name='Марек',confirmed_knowledge='Ключ у стража.')],player_words=[dict(to='Марек',words='Где ключ?')])

def test_schema_annotations_removed_without_losing_validation_or_property_names():
    schema=IntentPacket.model_json_schema();small=compact_schema(schema)
    assert len(json.dumps(small))<len(json.dumps(schema))
    assert small['required']==schema['required'] and small['additionalProperties'] is False
    assert small['$defs']['IntentAction']['properties']['after']==compact_schema(schema['$defs']['IntentAction']['properties']['after'])
    assert compact_schema({'properties':{'title':{'type':'string','title':'x'},'default':{'type':'string'}}})=={'properties':{'title':{'type':'string'},'default':{'type':'string'}}}

def test_shared_contracts_deduplicate_but_never_silently_override_a_definition():
    a={'$defs':{'X':{'type':'string'}},'type':'object'}
    assert shared_contracts({'A':a,'B':a})=={'$defs':{'X':{'type':'string'}},'models':{'A':{'type':'object'},'B':{'type':'object'}}}
    with pytest.raises(ValueError):shared_contracts({'A':a,'B':{'$defs':{'X':{'type':'number'}}}})

@pytest.mark.parametrize('text',['Арден входит в таверну.','Опасность близка.','Тихая таверна. '+('x'*800)])
def test_unsupported_hero_actions_threats_and_long_hidden_sentences_rejected(text):
    with pytest.raises(CanonViolation):verify(dict(description=text,dialogue=[],proposals=[]),PACKET)
    assert verify(fallback(PACKET),PACKET)

@pytest.mark.parametrize('format',['plain','structured'])
def test_artistic_prose_is_not_subject_to_literal_matching_or_second_calls(tmp_path,format):
    requests=[]
    bad='В свете очага дрожат мягкие тени.'
    raw=bad if format=='plain' else json.dumps(dict(description=bad,dialogue=[],proposals=[]),ensure_ascii=False)
    def respond(r):requests.append(r);return sse([{'choices':[{'delta':{'content':raw[:8]}}]},{'choices':[{'delta':{'content':raw[8:]}}]}])
    llm,p=setup(tmp_path,respond);p.enabled=True
    async def run():
        result=''.join([x async for x in llm.narrate_scene('',json.dumps(PACKET,ensure_ascii=False),narrative_format=format)])
        assert bad in result
        assert len(requests)==1 and llm.last_narrative_policy=='model_artistic'
        assert llm.metrics[-1]['status']=='ok'
        await llm.close()
    asyncio.run(run())

def test_npc_memory_is_audienced_and_stale_object_description_is_excluded():
    p=copy.deepcopy(PACKET);p['npc'][0].update(id='npc',confirmed_knowledge='')
    p['memory']=[dict(known_by=['someone_else'],truth='fact',kind='event',content='Секрет.'),dict(known_by=['npc'],truth='fact',kind='event',content='Мост разрушен.')]
    p['atmosphere']='Тихая таверна. Дверь закрыта.';p['object_states']=[dict(name='дверь',opened=True)]
    assert sources(p)['dialogue_sources']['Марек']==['Мост разрушен.']
    assert sources(p)['description_sources']==['Тихая таверна.']

@pytest.mark.parametrize('streaming',[True,False])
def test_visible_text_arrays_are_supported_without_promoting_reasoning(tmp_path,streaming):
    def respond(r):
        if streaming:return sse([{'choices':[{'delta':{'reasoning_content':'HIDDEN','content':[dict(type='text',text='Ответ.')]}}]}])
        return httpx.Response(200,json={'choices':[{'message':{'reasoning_content':'HIDDEN','content':[dict(type='text',text='Ответ.')]},'finish_reason':'stop'}]})
    llm,p=setup(tmp_path,respond);p.enabled=True;p.streaming=streaming
    async def run():
        assert ''.join([x async for x in llm.stream('','test')])=='Ответ.'
        await llm.close()
    asyncio.run(run())

def test_reasoning_only_is_precisely_diagnosed_no_retry_and_canonical_fallback(tmp_path):
    requests=[]
    def respond(r):requests.append(r);return sse([{'choices':[{'delta':{'reasoning_content':'DO NOT SHOW'},'finish_reason':'length'}]}])
    llm,p=setup(tmp_path,respond);p.enabled=True
    async def run():
        result=''.join([x async for x in llm.narrate_scene('',json.dumps(PACKET,ensure_ascii=False),narrative_format='plain')])
        assert result==render(fallback(PACKET)) and 'DO NOT SHOW' not in result
        assert len(requests)==1 and llm.last_narrative_policy=='engine_empty_response'
        assert llm.metrics[-1]['error_code']=='reasoning_without_answer'
        await llm.close()
    asyncio.run(run())

def test_ooc_never_gets_the_story_contract_or_changes_the_selected_role(tmp_path):
    requests=[]
    def respond(r):requests.append(json.loads(r.content));return sse([{'choices':[{'delta':{'content':'{}'}}]}])
    llm,p=setup(tmp_path,respond);p.enabled=True
    async def run():
        for selection in ('primary','secondary'):
            p.narrator_selection=selection
            async for _ in llm.stream('Ответ игроку вне роли.','{}',mode='gm'):pass
        assert all('Мастер D&D' not in x['messages'][0]['content'] for x in requests)
        assert requests[0]['model']==p.narrator.model and requests[1]['model']==p.secondary_narrator.model
        await llm.close()
    asyncio.run(run())

def test_gemini_thinking_off_is_rejected_before_any_paid_request(tmp_path):
    llm,p=setup(tmp_path,lambda r:pytest.fail('Unsupported thinking must be blocked before HTTP'))
    p.enabled=True;p.pdf_analysis_enabled=True;p.pdf_analyst.thinking='off'
    async def run():
        with pytest.raises(ValueError,match='LOW'):
            async for _ in llm.stream('','{}',mode='pdf_analyst'):pass
        assert not llm.metrics
        await llm.close()
    asyncio.run(run())

def test_reasoning_usage_without_reasoning_chunks_remains_diagnosed_after_200_calls(tmp_path):
    def respond(r):return sse([{'choices':[{'delta':{},'finish_reason':'length'}]},{'usage':{'prompt_tokens':10,'completion_tokens':9,'completion_tokens_details':{'reasoning_tokens':9}}}])
    llm,p=setup(tmp_path,respond);p.enabled=True;llm.metrics=[dict(id=str(i)) for i in range(200)]
    async def run():
        text=''.join([x async for x in llm.narrate_scene('',json.dumps(PACKET,ensure_ascii=False),narrative_format='plain')])
        assert text==render(fallback(PACKET)) and llm.last_narrative_policy=='engine_empty_response'
        assert llm.metrics[-1]['error_code']=='reasoning_without_answer' and len(llm.metrics)==200
        await llm.close()
    asyncio.run(run())

def test_declarative_candidates_evidence_edition_no_code_and_visual_unknown():
    text='Карта башни. Раздатка: письмо. Каждый час требуется проверка Телосложения Сл 12.'
    candidate=dict(page=1,quote=text,ability='constitution',dc=12,trigger='Каждый час',edition='dnd2014')
    value=AnalysisResult(suggestions=[],problems=[],classifications=[dict(page=1,kind='map',quote='Карта башни.',needs_visual_review=True)],mechanic_candidates=[candidate])
    assert validate_evidence(value,[dict(page=1,text=text)],'dnd2014')
    for changes in [dict(dc=13),dict(edition='dnd2024'),dict(trigger='При входе')]:
        bad=value.model_copy(deep=True);bad.mechanic_candidates[0]=type(bad.mechanic_candidates[0]).model_validate(dict(candidate,**changes))
        with pytest.raises(ValueError):validate_evidence(bad,[dict(page=1,text=text)],'dnd2014')
    with pytest.raises(ValueError):AnalysisResult(suggestions=[],problems=[],mechanic_candidates=[dict(candidate,code='print(1)')])
    bad=value.model_copy(deep=True);bad.classifications[0].needs_visual_review=False
    with pytest.raises(ValueError):validate_evidence(bad,[dict(page=1,text=text)])

def test_images_are_bounded_require_pricing_and_do_not_become_rule_evidence(tmp_path):
    from PIL import Image
    from solo.analysis_images import png_part,image_count
    image=png_part(Image.new('RGB',(320,200),'white'))
    with pytest.raises(ValueError):image_count([dict(type='image_url',image_url=dict(url='https://example.org/private.png'))])
    with pytest.raises(ValueError):image_count([image]*3)
    result=AnalysisResult(suggestions=[],problems=[],classifications=[dict(page=1,kind='map',source='image',needs_visual_review=True,transcription='Подпись, требует сверки')])
    assert validate_evidence(result,[dict(page=1,text='')],image_pages=[1])
    with pytest.raises(ValueError):validate_evidence(result,[dict(page=1,text='')])
    requests=[]
    def respond(r):requests.append(json.loads(r.content));return sse([{'choices':[{'delta':{'content':'{}'}}]}])
    llm,p=setup(tmp_path,respond);p.enabled=True;p.pdf_analysis_enabled=True
    async def run():
        with pytest.raises(ValueError,match='тарифом'):
            async for _ in llm.stream('',[dict(type='text',text='only selected page'),image],mode='pdf_analyst'):pass
        assert not requests
        p.pdf_analyst.image_per_million=Decimal(1)
        async for _ in llm.stream('',[dict(type='text',text='only selected page'),image],mode='pdf_analyst'):pass
        assert len(requests)==1 and requests[0]['messages'][1]['content'][1]['type']=='image_url'
        await llm.close()
    asyncio.run(run())

def test_local_selected_pdf_renders_only_requested_pages(tmp_path):
    import hashlib
    from PIL import Image
    from solo.adventure_library import AdventureLibrary
    from solo.analysis_images import render_selected,image_count
    path=tmp_path/'synthetic.pdf';Image.new('RGB',(320,200),'white').save(path,'PDF')
    library=AdventureLibrary(tmp_path/'library');sha=library.put_file(path)
    with library.db() as db:db.execute('INSERT INTO documents(id,format,bytes) VALUES(?,?,?)',(sha,'.pdf',path.stat().st_size))
    parts=render_selected(library,sha,[1]);assert image_count(parts)==1
    with pytest.raises(ValueError):render_selected(library,sha,[2])

def test_json_fence_normalization_never_repairs_or_executes_payloads():
    from solo.json_framing import unframe
    raw='```json\n{"suggestions":[],"problems":[]}\n```'
    body,kind=unframe(raw);assert kind=='json_fence' and AnalysisResult.model_validate_json(body)
    for bad in ['text before\n'+raw,raw+'\ntrailing',raw.replace('json','python'),raw.replace('[]}','[],"code":"exec(1)"}')]:
        body,kind=unframe(bad)
        with pytest.raises(ValueError):AnalysisResult.model_validate_json(body)

def test_image_only_source_can_use_local_extraction_failure_without_paid_qwen(tmp_path):
    from PIL import Image
    from solo.adventure_library import AdventureLibrary
    from solo.adventure_worker import run as import_source
    from solo.adventure_analysis import AdventureAnalysis,AnalysisRequest
    library=AdventureLibrary(tmp_path/'source-library');bundle=library.create('Synthetic image-only source')
    path=tmp_path/'source.png';Image.new('RGB',(200,160),'white').save(path)
    job=library.enqueue(bundle['id'],path,'source.png','maps');import_source(library.data,job['id'])
    doc=library.review(bundle['id'])['documents'][0]['id']
    with library.db() as db:db.execute('UPDATE pages SET content=? WHERE document=?',('',doc))
    requests=[];answer=dict(suggestions=[],problems=[],classifications=[dict(page=1,kind='unknown',source='image',needs_visual_review=True)])
    def respond(r):requests.append(json.loads(r.content));return sse([{'choices':[{'delta':{'content':json.dumps(answer)}}]}])
    llm,p=setup(tmp_path,respond);p.enabled=True;p.pdf_analysis_enabled=True;p.pdf_analyst.image_per_million=Decimal(1)
    service=AdventureAnalysis(library,llm);body=AnalysisRequest(document=doc,pages=[1],role='gemini',include_images=True,problem='Локальное извлечение текста пусто',request_id='image')
    async def execute():
        for page in (0,2):
            with pytest.raises(ValueError):await service.analyze(bundle['id'],body.model_copy(update=dict(pages=[page],consent_to_send=True)))
        with pytest.raises(ValueError,match='Подтвердите'):await service.analyze(bundle['id'],body)
        assert not requests
        body.consent_to_send=True;one=await service.analyze(bundle['id'],body);two=await service.analyze(bundle['id'],body)
        assert one['status']=='requires_review' and two['cached'] and len(requests)==1
        assert isinstance(requests[0]['messages'][1]['content'],list)
        await llm.close()
    asyncio.run(execute())
