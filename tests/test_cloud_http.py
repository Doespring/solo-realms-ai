"""HTTP/SSE workflow with an explicitly mocked remote provider, never paid inference."""
import json
from fastapi.testclient import TestClient
from solo.app import create_app
from test_cloud import configured,sse

def test_http_play_stream_replay_costs_model_switch_and_restore(tmp_path):
    remote=[]
    def respond(request):
        body=json.loads(request.content);remote.append(body)
        return sse([{'choices':[{'delta':{'content':'Огонь трещит. '}}]},
                    {'choices':[{'delta':{'content':'— Я помню, — говорит Ирина.'}}]},
                    {'usage':{'prompt_tokens':100,'completion_tokens':20,'cost':.01,'currency':'RUB'},'choices':[]}])
    llm,profile=configured(tmp_path,respond)
    app=create_app(tmp_path/'http.sqlite3',llm)
    with TestClient(app) as c:
        campaign=c.post('/api/campaigns',json={'name':'HTTP mock','hero':{'name':'Мирон'}}).json();cid=campaign['id']
        before=c.get('/api/campaigns/'+cid).json()['state']
        request={'request_id':'conversation','text':'Ирина, что известно о твоём брате?'}
        first=c.post('/api/campaigns/'+cid+'/play',json=request).json();event=first['event']['id']
        result=c.post('/api/events/'+event+'/narrate',json={'regenerate':False})
        assert result.status_code==200 and result.headers['content-type'].startswith('text/event-stream')
        frames=[json.loads(s[6:]) for s in result.text.splitlines() if s.startswith('data: ')]
        # One release, not a token-by-token stream: text reaches the table only
        # after the sanitiser saw all of it (see solo/game.py, narrate).
        assert [r['type'] for r in frames]==['token','done']
        assert frames[0]['text']=='Огонь трещит. — Я помню, — говорит Ирина.'
        assert frames[-1]['source']=='model_artistic'
        assert 'Я помню' in ''.join(f.get('text','') for f in frames)
        snapshot=c.get('/api/campaigns/'+cid).json()
        assert snapshot['history'][0]['narrative_data']['presentation_only']
        assert snapshot['state']['facts']==before['facts']
        assert c.post('/api/campaigns/'+cid+'/play',json=request).json()['event']['id']==event
        replay=c.post('/api/events/'+event+'/narrate',json={'regenerate':False}).text
        assert '"replayed":true' in replay and len(remote)==1
        costs=c.get('/api/ai/costs',params={'campaign':cid}).json()
        assert costs['campaign'][0]['cost']=='0.01' and len(costs['calls'])==1
        assert costs['calls'][0]['turn_ref']=='conversation' and costs['calls'][0]['campaign']==cid
        body=profile.model_dump(mode='json');body['narrator']['model']='another-mock-model'
        assert c.post('/api/ai/profiles',json=body).status_code==200
        c.post('/api/events/'+event+'/narrate',json={'regenerate':True})
        assert len(remote)==2 and remote[-1]['model']=='another-mock-model'
        assert c.get('/api/campaigns/'+cid).json()['state']['turn']==before['turn']+1
        save=c.post('/api/campaigns/'+cid+'/saves',json={'name':'После разговора'}).json()
        assert c.post('/api/campaigns/'+cid+'/undo').status_code==200
        assert c.get('/api/campaigns/'+cid).json()['state']==before
        checkpoint=save.get('id',save.get('checkpoint')) if isinstance(save,dict) else save
        assert checkpoint
        assert c.post('/api/campaigns/'+cid+'/restore/'+checkpoint).status_code==200
        assert c.get('/api/campaigns/'+cid).json()['state']['turn']==before['turn']+1
        # Billing survives a game rollback; a real provider cannot uncharge an undo.
        assert c.get('/api/ai/costs',params={'campaign':cid}).json()['campaign'][0]['cost']=='0.02'

def test_http_connection_profile_is_not_a_fake_success_without_key(tmp_path):
    app=create_app(tmp_path/'offline.sqlite3')
    with TestClient(app) as c:
        health=c.get('/api/health').json()
        assert health['cloud_only'] and health['llm'] is None and health['readiness']['status']=='unconfigured'
        assert c.get('/api/ai/costs').json()['calls']==[]
        assert c.get('/').status_code==200 and c.get('/static/cloud.js').status_code==200
