import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.llm import MockLLM
from solo.navigation import NavigationPolicy,DOCUMENTS

@pytest.mark.parametrize('port',[19161,21587])
def test_top_level_routes_documents_and_downloads_are_explicit(port):
    policy=NavigationPolicy(port);origin=policy.origin
    assert policy.classify(origin+'/')==('app','')
    assert policy.classify(origin+'/#preparation')==('app','preparation')
    assert policy.classify(origin+'/static/preparation-guide.md')==('document','preparation-guide')
    assert policy.classify(origin+'/static/player-help.txt')==('document','player-help')
    assert policy.classify(origin+'/static/visual/ATTRIBUTION.md')==('document','asset-attribution')
    assert policy.classify(origin+'/desktop/bootstrap?token=synthetic',bootstrap=True)[0]=='bootstrap'
    assert policy.classify(origin+'/desktop/bootstrap?token=synthetic')[0]=='blocked'
    assert policy.classify(origin+'/api/data/export')[0]=='download'
    assert policy.classify('blob:'+origin+'/synthetic')[0]=='download'
    for path in ['/static/play.js','/api/campaigns','/api/adventure/review/private','/static/unknown.md','/static/unknown.txt','/static/unknown.pdf','/?secret=1','/#unknown']:
        assert policy.classify(origin+path)[0]=='blocked'

@pytest.mark.parametrize('url',['file:///C:/secret.txt','javascript:alert(1)','data:text/html,test','http://127.0.0.1:8001/','http://localhost:19161/','https://localhost/','https://127.0.0.1/','https://10.0.0.1/','https://[::1]/','https://example.org:8443/','https://user:password@example.org/','https://host.local/','https://example.org/%00x','https://example.org/\n','https://example.org\\@localhost/','blob:http://127.0.0.1:8001/unknown'])
def test_unknown_unsafe_and_other_process_navigation_blocked(url):assert NavigationPolicy(19161).classify(url)[0]=='blocked'

def test_external_https_needs_confirmation_and_documents_recover_shell(tmp_path,monkeypatch):
    opened=[];monkeypatch.setattr('webbrowser.open',lambda url,**kwargs:opened.append(url) or True)
    app=create_app(tmp_path/'nav.sqlite3',MockLLM(),local_port=21587)
    with TestClient(app) as client:
        for id,definition in DOCUMENTS.items():
            raw=client.get('/api/help/'+id);assert raw.status_code==200 and raw.json()['text']
            for url in ('/help/'+id,'/static/'+definition['path']):
                response=client.get(url);assert response.status_code==200 and 'text/html' in response.headers['content-type'] and 'navigation.js' in response.text
        assert client.get('/api/help/unknown').status_code==404
        assert client.get('/static/play.js').status_code==200  # subresources still work
        assert client.get('/api/campaigns').status_code==200
        url='https://www.python.org/'
        assert client.post('/api/navigation/classify',json={'url':url}).json()['kind']=='external'
        assert client.post('/api/navigation/external',json={'url':url}).status_code==409 and not opened
        assert client.post('/api/navigation/external',json={'url':'file:///secret','confirm':True}).status_code==409 and not opened
        assert client.post('/api/navigation/external',json={'url':url,'confirm':True}).status_code==200 and opened==[url]
        assert client.post('/api/navigation/classify',json={'url':'http://127.0.0.1:21587/'}).json()['kind']=='app'
    assert app.state.costs.summary()['calls']==[]
