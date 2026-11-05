"""Native simple connection flow with a synthetic DPAPI key and metadata transport."""
import asyncio,json,time
import httpx

def cloud_smoke(client,window,app):
    from solo.ai_roles import routerai_preset
    from solo.cloud import CloudLLM
    p=routerai_preset();p.name='EXE smoke · Simple connection'
    client.post('/api/ai/profiles',json=p.model_dump(mode='json')).raise_for_status()
    client.post('/api/ai/profiles/'+p.id+'/activate').raise_for_status()
    requests=[]
    def respond(request):
        requests.append(request)
        if request.method=='POST':
            return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: '+json.dumps({'choices':[{'delta':{'content':'Готово.'}}],'usage':{'prompt_tokens':20,'completion_tokens':4}})+'\n\ndata: [DONE]\n\n')
        if request.url.path.endswith('/endpoints'):
            return httpx.Response(200,json={'data':{'endpoints':[dict(tag='synthetic-smoke-route',context_length=65536,max_completion_tokens=8192,status=0,
                supported_parameters=['max_tokens','temperature','response_format','reasoning'],supported_apis=['chat'],
                pricing={'prompt':.000001,'completion':.000001},pricing_units={'prompt':'token','completion':'token'})]}})
        return httpx.Response(200,json={'data':[dict(id=getattr(p,role).model) for role in ('interpreter','narrator','secondary_narrator','pdf_analyst')]})
    old=app.state.game.llm
    llm=CloudLLM(app.state.cloud_settings,app.state.costs,client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    app.state.game.llm=llm
    def until(expression):
        end=time.monotonic()+8
        while time.monotonic()<end:
            if window.evaluate_js(expression):return
            time.sleep(.05)
        raise AssertionError('Simple cloud UI timeout: '+expression)
    try:
        before=client.get('/api/ai/costs').json()['calls']
        window.evaluate_js("document.querySelector('[data-view=cloud]').click();true")
        until("document.querySelector('#api-profile-list').value==='"+p.id+"'&&!document.querySelector('#api-simple-key').disabled&&!document.querySelector('#api-advanced').open")
        assert window.evaluate_js("!document.querySelector('#tabletop-advanced').contains(document.querySelector('[data-view=cloud]'))")
        window.evaluate_js("document.querySelector('#api-simple-key').value='synthetic-smoke-never-send';document.querySelector('#api-simple').requestSubmit();true")
        until("!document.querySelector('#api-simple button[type=button]').disabled")
        assert len(requests)==5
        window.evaluate_js("document.querySelector('#api-simple button[type=button]').click();true")
        until("document.querySelector('#api-enable-confirm').open")
        window.evaluate_js("const d=document.querySelector('#api-enable-confirm'),n=d.querySelector('input[type=number]');n.value='1';n.dispatchEvent(new Event('input'));const c=d.querySelector('input[type=checkbox]');c.checked=true;c.dispatchEvent(new Event('change'));d.querySelectorAll('button')[1].click();true")
        until("!document.querySelector('#api-enable-confirm').open&&document.querySelector('#api-simple-status').textContent.includes('Подключение проверено')")
        public=client.get('/api/ai/profiles').json();saved=next(row for row in public['profiles'] if row['id']==p.id)
        assert saved['enabled'] and saved['key_present'] and saved['pdf_analyst']['thinking']=='low'
        assert window.evaluate_js("document.querySelector('#api-simple-key').value===''")
        assert 'synthetic-smoke-never-send' not in json.dumps(public)
        assert len(client.get('/api/ai/costs').json()['calls'])==len(before)+1
        assert sum(r.method=='POST' for r in requests)==1
        saved.pop('key_present');saved['enabled']=False;client.post('/api/ai/profiles',json=saved).raise_for_status()
        return dict(simple_provider_key_flow=True,automatic_routes_reasoning_limits=True,advanced_closed=True,ui_explicit_enable=True,confirmation_limit='1 RUB',synthetic_dpapi_key=True,key_not_echoed=True,metadata_gets=sum(r.method=='GET' for r in requests),mock_connection_generations=1,paid_calls=0,disabled_after_test=True)
    finally:
        app.state.game.llm=old;asyncio.run(llm.close())
