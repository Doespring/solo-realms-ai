import json,time,hashlib
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.preparation_format import decode,diagnostics,split_pages,source_input
from solo.cloud import CloudUnavailable
from test_preparation_pipeline import PreparationFixture,new,terminal

def test_format_repairs_change_identifiers_not_quoted_facts():
    body=dict(title='Книга',entities=[dict(key='Гринест',kind='location',data=dict(name='Гринест'),evidence=[dict(document='a'*64,page=1,quote='Гринест')])])
    result,changes=decode('```json\n'+json.dumps(body,ensure_ascii=False)+'\n```')
    assert result.entities[0].key.startswith('entity_') and result.entities[0].data==body['entities'][0]['data']
    assert result.entities[0].evidence[0].quote=='Гринест' and 'entities.key_ascii_identifier' in changes
    second,_=decode(json.dumps(json.dumps(body,ensure_ascii=False)));assert second==result
    for bad in ('{"entities":[', '{"entities":[],"entities":[]}',json.dumps(dict(body,unknown='not allowed'))):
        with pytest.raises(ValueError):decode(bad)

def test_diagnostics_include_field_and_reason_without_source_values():
    try:decode(json.dumps(dict(entities=[dict(key='ok',kind='secret_invalid_GM_FACT',data={})])))
    except ValueError as exc:
        errors=diagnostics(exc);assert errors[0]['field']=='entities.0.kind'
        assert errors[0]['reason']=='literal_error' and 'secret_invalid_GM_FACT' not in json.dumps(errors)

def test_source_span_restores_exact_page_and_quote_without_fuzzy_matching():
    original='Точный источник.\r\nОтрицание: дверь не заперта.'
    wire,spans,schema=source_input([dict(document='a'*64,page=7,offset=0,text=original)])
    assert wire[0]['text']=='[S001] '+original
    body=dict(entities=[dict(key='door',kind='location',data=dict(name='дверь'),evidence=[dict(source_span='S001')])])
    result,changes=decode(json.dumps(body),spans);proof=result.entities[0].evidence[0]
    assert proof.page==7 and proof.quote==original and proof.document=='a'*64
    assert 'verbatim_source_span' in changes
    body['entities'][0]['evidence'][0]['source_span']='S999'
    with pytest.raises(ValueError) as exc:decode(json.dumps(body),spans)
    assert diagnostics(exc.value)[0]['field']=='entities.0.evidence.0.source_span'

def test_redundant_known_refs_are_reduced_without_retry_or_added_facts():
    from solo.source_extraction import parse
    spans={'S'+str(i).zfill(3):dict(document='a'*64,page=i,quote='Место А. Проверенный текст.') for i in range(1,12)}
    result,changes=parse(json.dumps(dict(entries=[dict(kind='location',name='Место А',source_spans=list(spans),start=True)])),spans,'dnd2014')
    assert len(result.entities[0].evidence)==1 and result.entities[0].data==dict(name='Место А')
    assert 'redundant_source_references_removed' in changes
    with pytest.raises(ValueError):parse('{"entries":[],"entries":[]}',spans,'dnd2014')

def test_missing_reference_array_delimiter_is_repaired_without_inventing_values():
    from solo.source_extraction import parse
    spans={'S012':dict(document='a'*64,page=7,quote='Элтурель')}
    raw='{"entries":[{"kind":"location","name":"Элтурель","source_spans":["S012","start":true}]}'
    result,changes=parse(raw,spans,'dnd2014');assert result.start=='Элтурель'
    assert result.entities[0].data==dict(name='Элтурель') and 'misclosed_source_spans_array' in changes
    with pytest.raises(ValueError):parse(raw.replace('"start":true','"execute":"code"'),spans,'dnd2014')

def test_split_preserves_all_characters_and_offsets():
    pages=[dict(document='a'*64,page=2,offset=80,text='ab\n'*2000),dict(document='a'*64,page=3,offset=0,text='xyz'*2000)]
    children=split_pages(pages);assert len(children)==2
    assert ''.join(p['text'] for c in children for p in c)==''.join(p['text'] for p in pages)
    for p in [p for c in children for p in c]:
        original=next(o for o in pages if o['page']==p['page']);start=p['offset']-original['offset']
        assert p['text']==original['text'][start:start+len(p['text'])]

def test_tariff_refresh_reuses_parts_but_generation_changes_do_not(tmp_path):
    from solo.preparation_pipeline import PreparationPipeline
    from solo.storage import Store
    from solo.preparation import Preparation
    from solo.adventure_library import AdventureLibrary
    from solo.creation import CreationService
    store=Store(tmp_path/'solo.sqlite3');prep=Preparation(tmp_path,store);service=CreationService(prep,AdventureLibrary(tmp_path),None)
    pipeline=PreparationPipeline(service,PreparationFixture(),store);draft=service.new()['id']
    with prep.connection() as db:db.execute('INSERT INTO creation_runs(id,draft,request,fingerprint,status,created) VALUES(?,?,?,?,?,?)',('run',draft,'r','f','queued',0))
    identity=dict(draft=draft,prompt='source',parameters=dict(model='qwen',temperature=.2,max_tokens=1800,price_checked_at='yesterday',input_per_million='1',output_per_million='2',parameters=['temperature','max_tokens']))
    first=pipeline.ensure_part('run',identity,[],True,0)
    changed={**identity,'parameters':{**identity['parameters'],'price_checked_at':'today','input_per_million':'3','parameters':['max_tokens','temperature']}}
    assert pipeline.ensure_part('run',changed,[],True,0)==first
    changed['parameters']['temperature']=.5
    assert pipeline.ensure_part('run',changed,[],True,0)!=first

class BlockFixture(PreparationFixture):
    def __init__(self):super().__init__();self.fail=True;self.calls=[]
    async def stream(self,system,user,**kwargs):
        import re
        payload=json.loads(user);self.calls.append(payload['batch']);self.metrics.append(dict(mock=True))
        if self.fail and payload['batch']==1:raise CloudUnavailable('HTTP 503 — синтетический сбой блока')
        page=payload['pages'][0];name=re.search(r'Место[АБВ]',page['text']).group()
        result=dict(title='Тест',ruleset='dnd2014',start=name,entities=[dict(key='loc'+name[-1],kind='location',data=dict(name=name),evidence=[dict(source_span=re.search(r'\[(S\d+)\]',page['text'])[1])])])
        yield json.dumps(result,ensure_ascii=False)

def test_failed_middle_block_does_not_discard_or_rebill_good_blocks_on_resume(tmp_path,monkeypatch):
    # Тест про повторный вызов одного блока, а не про размер секции: при
    # пределе 40 тыс. знаков три файла склеиваются в один вызов, и «средний
    # блок упал» не воспроизводится. Масштаб задаётся явно.
    monkeypatch.setenv('SOLO_PREPARATION_SECTION_CHARACTERS','6000')
    llm=BlockFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as c:
        draft,url=new(c,'По приложенным файлам, D&D 2014')
        digests=[]
        for letter in 'АБВ':
            raw=('Место'+letter+' описано здесь.\n')*600;digests.append(hashlib.sha256(raw.encode()).hexdigest())
            response=c.post(url+'/files',files={'file':('source'+letter+'.txt',raw.encode())});response.raise_for_status()
            job=response.json()['files'][0]['job']['id'];deadline=time.monotonic()+12
            while c.get('/api/adventure/jobs/'+job).json()['status']!='complete':assert time.monotonic()<deadline;time.sleep(.02)
        first=c.post(url+'/prepare',json=dict(request_id='first',ruleset='dnd2014',consent_to_send=True)).json()
        result=terminal(c,first['id']);assert result['status']=='partial' and result['done']==result['total']-1
        assert any(i>1 for i in llm.calls),llm.calls
        assert result['errors'][0]['field']=='$' and not result['ready']
        original_calls=len(llm.calls)
        local=c.post(url+'/readiness',json={});local.raise_for_status()
        assert local.json()['status']=='blocked' and not local.json()['ready']
        assert local.json()['entry_readiness']['scope']=='entry' and len(llm.calls)==original_calls
        assert c.get('/api/creation/runs/'+first['id']).json()['status']=='partial'
        llm.fail=False
        second=c.post(url+'/prepare',json=dict(request_id='resume',ruleset='dnd2014',consent_to_send=True)).json()
        resumed=terminal(c,second['id']);assert resumed['done']==resumed['total'] and len(llm.calls)==original_calls+1
        assert llm.calls[-1]==1
        for sha in digests:assert c.app.state.adventures.blob(sha).exists()
        with c.app.state.preparation.connection() as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
            assert db.execute('SELECT count(*) FROM creation_parts WHERE status="validated"').fetchone()[0]==result['total']

def test_truncated_block_splits_without_losing_text_and_survives_restart(tmp_path):
    class Limited(BlockFixture):
        def __init__(self):super().__init__();self.fail=False;self.first=True
        async def stream(self,system,user,**kwargs):
            if self.first:
                self.first=False;self.calls.append(0);self.metrics.append(dict(mock=True,hit_token_limit=True));yield '{"entries":[';return
            async for text in super().stream(system,user,**kwargs):yield text
    llm=Limited();db_path=tmp_path/'solo.sqlite3'
    with TestClient(create_app(db_path,llm)) as c:
        draft,url=new(c,'Тест D&D 2014');raw=('МестоА описано здесь.\n'*350).encode()
        uploaded=c.post(url+'/files',files={'file':('one.txt',raw)}).json();job=uploaded['files'][0]['job']['id'];deadline=time.monotonic()+10
        while c.get('/api/adventure/jobs/'+job).json()['status']!='complete':assert time.monotonic()<deadline;time.sleep(.02)
        run=c.post(url+'/prepare',json=dict(request_id='split',ruleset='dnd2014',consent_to_send=True)).json();result=terminal(c,run['id'])
        # Число секций теперь задаёт нарезка по главам, а не 16 000 символов:
        # проверяем свойство (текст сохранён, деление случилось один раз), не магию.
        assert result['ready'] and result['done']==result['total']>=2 and len(llm.calls)==result['total']+1
        with c.app.state.preparation.connection() as db:
            parent=db.execute('SELECT source,children FROM creation_parts WHERE status="split"').fetchone();children=[db.execute('SELECT source FROM creation_parts WHERE id=?',(id,)).fetchone()[0] for id in json.loads(parent['children'])]
            assert ''.join(p['text'] for child in children for p in json.loads(child)['pages'])==''.join(p['text'] for p in json.loads(parent['source'])['pages'])
            assert db.execute('SELECT count(*) FROM creation_part_attempts WHERE status="failed"').fetchone()[0]==1
    before=len(llm.calls)
    with TestClient(create_app(db_path,llm)) as c:
        run=c.post(url+'/prepare',json=dict(request_id='restart',ruleset='dnd2014',consent_to_send=True)).json()
        assert terminal(c,run['id'])['ready'] and len(llm.calls)==before
