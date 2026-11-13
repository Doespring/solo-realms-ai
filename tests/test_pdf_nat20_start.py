"""Synthetic PDF -> real preparation/validation -> explicit Nat20 start. Offline."""
import os
os.environ['SOLO_TEST']='1'

import io
import json
from pathlib import Path
import subprocess
import sys
import time

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
import pytest

from scripts.playable_ai_readiness import mock_cloud
from solo.app import create_app, ROOT
from solo.narrative_grounding import fallback
from solo.preparation import Draft
from solo.prototype_prepared import launch_prepared, prepared_start_readiness
from test_cloud import sse


OPENING='You stand in the Forge. Dorn, a smith, waits here.'
KNOWLEDGE='The bridge is closed after the rain.'
SOURCE=['D&D 2014. Forge and Tower.', 'Read aloud: '+OPENING,
        'Dorn is in Forge. Dorn knows: '+KNOWLEDGE,
        'Read aloud: The Tower rises beyond the path.',
        'Path: Forge to Tower, 10 meters, 1 minute.', 'Unbound ritual.', 'GM_SECRET_KEEP_PRIVATE']


def synthetic_pdf():
    """An actual text PDF, not a prebuilt ScenarioPackage disguised as extraction."""
    writer=PdfWriter();page=writer.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject()
    stream.set_data(('BT /F1 11 Tf 30 750 Td 16 TL '+' '.join('('+line.replace('(','\\(').replace(')','\\)')+') Tj T*' for line in SOURCE)+' ET').encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO();writer.write(output);return output.getvalue()


def proposal(packet,case):
    if case=='names':
        return dict(title='Forge and Tower',entries=[dict(kind='location',name='Forge',start=True,source_spans=['S001'])])
    proof=[dict(source_span='S001')]
    value=dict(title='Forge and Tower',ruleset='dnd2014',start='Forge',entities=[
        dict(key='forge',kind='location',data=dict(name='Forge',description=OPENING,secrets=['GM_SECRET_KEEP_PRIVATE']),evidence=proof),
        dict(key='tower',kind='location',data=dict(name='Tower',description='The Tower rises beyond the path.'),evidence=proof),
        dict(key='dorn',kind='npc',location='Forge',data=dict(name='Dorn',character='a smith',known=KNOWLEDGE),evidence=proof),
        dict(key='path',kind='route',data={'from':'Forge','to':'Tower','meters':10,'minutes':1,'description':'Path'},evidence=proof)])
    if case=='missing':
        value['entities'].append(dict(key='required-npc',kind='npc',location='Absent',data=dict(name='Dorn'),evidence=proof))
    if case in ('optional','mandatory'):
        value['entities'].append(dict(key='ritual',kind='special_rule',data=dict(name='Unbound ritual'),evidence=proof,essential=case=='mandatory'))
    if case=='start-event':
        value['entities'].append(dict(key='start-event',kind='event',data=dict(id='ritual-start',category='mandatory',
            conditions=[dict(path='location',value='Forge')],effects=[dict(kind='notice',text='Unbound ritual')]),evidence=proof))
    return value


def prepare(tmp_path,case='full'):
    requests=[]
    def respond(request):
        body=json.loads(request.content);packet=json.loads(body['messages'][-1]['content']);requests.append(packet)
        if packet.get('task') in ('campaign-prepare-v1','source-extract-v1'):
            assert 'Dorn is in Forge' in packet['pages'][0]['text']
            value=proposal(packet,case)
        elif 'player_intent' in packet:
            text=packet['player_intent']
            if 'Спрашиваю' in text:value=dict(status='conversation',target=packet['context']['scene']['npc'][0]['id'])
            elif 'Проверяю' in text:value=dict(status='command',action=dict(kind='check',check_id='save-dex'),message='')
            else:value=dict(status='observe',target='')
        else:value=fallback(packet)
        return sse([dict(choices=[dict(delta=dict(content=json.dumps(value,ensure_ascii=False)))]),
                    dict(choices=[],usage=dict(prompt_tokens=1000,completion_tokens=200))])
    llm,_=mock_cloud(tmp_path,respond)
    app=create_app(tmp_path/'game.sqlite3',llm,experimental=True)
    client=TestClient(app);client.__enter__()
    try:
        draft=client.post('/api/creation/drafts').json();did=draft['id'];url='/api/creation/drafts/'+did
        saved=client.put(url,json=dict(description='Synthetic source adventure, D&D 2014; mechanics chosen separately.',revision=draft['revision']))
        assert saved.status_code==200,saved.text
        uploaded=client.post(url+'/files',files={'file':('synthetic-forge.pdf',synthetic_pdf(),'application/pdf')})
        assert uploaded.status_code==200,uploaded.text
        job=uploaded.json()['files'][0]['job']['id'];deadline=time.monotonic()+20
        while client.get('/api/adventure/jobs/'+job).json()['status']!='complete':
            assert time.monotonic()<deadline;time.sleep(.02)
        request=dict(request_id='structure',ruleset='dnd2014',consent_to_send=True,world_structure=case!='names')
        response=client.post(url+'/prepare',json=request)
        assert response.status_code==200,response.text
        run_id=response.json()['id']
        while True:
            status=client.get('/api/creation/runs/'+run_id).json()
            if status['status'] not in ('queued','running'):break
            assert time.monotonic()<deadline;time.sleep(.02)
        return app,client,did,run_id,status,requests
    except BaseException:
        client.__exit__(*sys.exc_info());raise


def test_real_pdf_confirmed_world_2014_with_explicit_2024_rogue_and_continuation(tmp_path):
    from test_nat20_prototype import build,encounter
    app,client,did,run_id,status,requests=prepare(tmp_path)
    try:
        assert status['ready'],status
        prep=app.state.preparation;pipeline=app.state.preparation_pipeline
        readiness=prepared_start_readiness(prep,did)
        assert readiness['world_ready'] and not readiness['ready'] and readiness['source_ruleset']=='dnd2014'
        assert 'GM_SECRET' not in json.dumps(readiness)
        before=prep.get(did)
        confirmed=pipeline.confirm_world(did,run_id=run_id,expected_revision=before['revision'],confirm=True)
        assert pipeline.confirm_world(did,run_id=run_id,expected_revision=before['revision'],confirm=True)==confirmed
        body=Draft.model_validate(confirmed['body']);body.hero=dict(name='Рив',class_name='rogue',species='human');body.background_confirmed=True
        saved=prep.save(did,body,confirmed['revision'])
        args=dict(confirm=True,expected_revision=saved['revision'],build=build('rogue'),encounters={'authored-rats':encounter()},seed=5)
        with pytest.raises(ValueError,match='Явно выберите'):
            launch_prepared(prep,did,**args)
        assert not app.state.store.list_campaigns()
        result=launch_prepared(prep,did,**args,mechanics_ruleset='dnd2024',rules_confirmed=True)
        cid=result['campaign']['id'];store=app.state.store;initial=store.campaign(cid)['state']
        assert initial['ruleset']['id']=='dnd2024'
        assert initial['preparation']['source_ruleset']=='dnd2014' and prep.get(did)['body']['ruleset']=='dnd2014'
        assert initial['preparation']['source_documents'] and len(initial['locations'])==2
        assert initial['geography']['routes']
        assert launch_prepared(prep,did,**args,mechanics_ruleset='dnd2024',rules_confirmed=True)['campaign']['id']==cid
        with pytest.raises(ValueError,match='другим Nat20'):
            launch_prepared(prep,did,**dict(args,build=build('wizard')),mechanics_ruleset='dnd2024',rules_confirmed=True)
        # Recover an installed world whose separate draft linkage was interrupted.
        with prep.connection() as db:db.execute('UPDATE drafts SET campaign=NULL WHERE id=?',(did,))
        recovered=launch_prepared(prep,did,**args,mechanics_ruleset='dnd2024',rules_confirmed=True)
        assert recovered['campaign']['id']==cid and len(store.list_campaigns())==1
        assert store.campaign(cid)['state']==initial
        assert KNOWLEDGE not in json.dumps(app.state.game.compiler.command_context(store.campaign(cid)))
        for key,text,expected in [('look','Осматриваюсь','observation'),('talk','Спрашиваю Dorn про bridge','conversation'),('check','Проверяю ловкость','resolved')]:
            response=client.post('/api/campaigns/'+cid+'/command-turn',json=dict(request_id=key,text=text,expected_version=store.campaign(cid)['version']))
            assert response.status_code==200,response.text
            answer=response.json();assert answer['status']==expected,answer
            assert 'GM_SECRET' not in json.dumps(answer)
            if key=='talk':assert KNOWLEDGE in answer['narrative']
            if expected!='resolved':
                assert store.campaign(cid)['state']['nat20']==initial['nat20']
                assert store.campaign(cid)['state']['facts']==initial['facts']
        store.save(cid,'После исследования')
        native=store.campaign(cid)['state']['nat20']
        before_version=store.campaign(cid)['version']
        program='''
import os;os.environ['SOLO_TEST']='1'
import sys,json
from solo.storage import Store
from solo.nat20_api import Nat20GameEngine
from solo.game_commands import GameCommand
s=Store(sys.argv[1]);api=Nat20GameEngine(s);cid=sys.argv[2]
previous=api.restore(cid)['runtime']
event=api.execute(cid,GameCommand(request_id='continued',expected_version=s.campaign(cid)['version'],action=dict(kind='check',check_id='save-wis')))
print(json.dumps(dict(previous=previous,version=s.campaign(cid)['version'],events=event['event'].model_dump(mode='json') if hasattr(event.get('event'),'model_dump') else bool(event))))
'''
        child=subprocess.run([sys.executable,'-B','-c',program,str(store.path),cid],cwd=ROOT,env={**os.environ,'SOLO_TEST':'1','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,encoding='utf8',timeout=45)
        assert child.returncode==0,child.stderr
        continued=json.loads(child.stdout)
        assert continued['previous']==native and continued['version']==before_version+1 and continued['events']
        assert store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
        assert sum(p.get('task')=='campaign-prepare-v1' for p in requests)==1
        # A changed player description cannot reuse an old confirmation silently.
        with prep.connection() as db:
            private=prep.get(did)['body'];private['description']='Changed source selection intent'
            db.execute('UPDATE drafts SET body=? WHERE id=?',(json.dumps(private),did))
        assert not prepared_start_readiness(prep,did)['world_ready']
    finally:client.__exit__(None,None,None)


@pytest.mark.parametrize('case,reason',[('names','имён недостаточно'),('mandatory','Механический обработчик'),('start-event','Обязательная процедура старта ritual-start')])
def test_incomplete_real_source_cannot_be_promoted(tmp_path,case,reason):
    app,client,did,run_id,status,requests=prepare(tmp_path,case)
    try:
        readiness=prepared_start_readiness(app.state.preparation,did)
        assert not readiness['world_ready'] and reason in ' '.join(readiness['reasons'])
        before=app.state.preparation.get(did)
        with pytest.raises(ValueError,match=reason):
            app.state.preparation_pipeline.confirm_world(did,expected_revision=before['revision'],run_id=run_id,confirm=True)
        assert app.state.preparation.get(did)==before and not app.state.store.list_campaigns()
    finally:client.__exit__(None,None,None)


def test_unconfirmed_npc_place_blocks_the_relation_not_the_world(tmp_path):
    """An NPC with an unconfirmed place quarantines its relation, not the start.

    The validator keeps the NPC out of every location and records
    `location_status='unresolved'`, so the world itself is promotable and the
    confirmed start no longer pretends a place the source never gave
    (`test_unresolved_npc_start.py` pins that decision). Refusing the whole
    adventure for one invented address would close books that are otherwise
    complete, so this case is no longer in the refusal table; the NPC relation
    is still reported as a blocked operation.
    """
    app,client,did,run_id,status,requests=prepare(tmp_path,'missing')
    try:
        readiness=prepared_start_readiness(app.state.preparation,did)
        blocked=[row for row in readiness['blocked_operations'] if row.get('key')=='required-npc']
        assert blocked and blocked[0]['kind']=='npc',readiness
        assert blocked[0]['reason']=='Местонахождение NPC не подтверждено источником',blocked
        result=app.state.preparation_pipeline.world_result(did)[2]
        assert not any(q.get('key')=='required-npc' and q.get('essential') for q in result['quarantine'])
        assert readiness['world_ready'],readiness
        confirmed=app.state.preparation_pipeline.confirm_world(did,expected_revision=readiness['revision'],run_id=run_id,confirm=True)
        assert confirmed['body']['world_confirmation']['run_id']==run_id
    finally:client.__exit__(None,None,None)


def test_optional_unbound_mechanic_does_not_block_confirmed_world(tmp_path):
    app,client,did,run_id,status,requests=prepare(tmp_path,'optional')
    try:
        ready=prepared_start_readiness(app.state.preparation,did)
        assert ready['world_ready'] and not ready['ready']
        assert any(row['kind']=='special_rule' for row in ready['blocked_operations'])
        confirmed=app.state.preparation_pipeline.confirm_world(did,run_id=run_id,expected_revision=ready['revision'],confirm=True)
        assert prepared_start_readiness(app.state.preparation,did)['ready']
        assert confirmed['body']['ruleset']=='dnd2014'
    finally:client.__exit__(None,None,None)

def test_verified_entry_world_can_be_confirmed_and_entered_by_a_hero(tmp_path):
    """The prepared entry scene is a real start, not a dead end after payment."""
    app,client,did,run_id,status,requests=prepare(tmp_path)
    try:
        assert status['ready']
        pipeline=app.state.preparation_pipeline
        readiness=prepared_start_readiness(app.state.preparation,did)
        assert readiness['world_ready'] and not readiness['ready']
        pipeline.confirm_world(did,run_id=run_id,expected_revision=readiness['revision'],confirm=True)
        assert prepared_start_readiness(app.state.preparation,did)['ready']
        from nat20_builds import legal
        body=dict(name='Рив',build=legal(class_slug='rogue',species_slug='human',level=1,equipment=['dagger','potion-of-healing']),
            prepared_spells=[],adventure='prepared:'+did,description='',encounter_monsters=[],mechanics_ruleset='dnd2024',
            rules_confirmed=True,hero_confirmed=True,background_confirmed=True,
            adventure_revision=app.state.preparation.get(did)['revision'],request_id='entry-start')
        created=client.post('/api/prototype/campaigns',json=body)
        assert created.status_code==200,created.text
        state=client.get('/api/campaigns/'+created.json()['id']).json()['state']
        # The hero starts inside the verified source scene, not in an empty sandbox.
        assert state['location'] and any(p['name']==state['location'] for p in state['locations'])
        assert state['preparation']['source_documents']
    finally:client.__exit__(None,None,None)

