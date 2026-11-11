"""Frozen app self-checks on its own synthetic profile; no external browser automation."""
import hashlib,json,time
from pathlib import Path

PDF_SMOKE_MARKER='.pdf-world-smoke.json'

def validate_pdf_smoke_profile(directory,phase):
    """QA-only: a new prepare profile, or the exact marked profile for continuation."""
    from .prototype_profile import default_profile,check_database
    path=check_database(directory)
    if path in (default_profile().resolve(),default_profile(True).resolve()) or Path(directory).is_symlink():
        raise ValueError('PDF smoke refuses a normal profile')
    if phase=='prepare':
        if path.exists() and any(path.iterdir()):raise ValueError('PDF smoke prepare requires a new empty profile')
    elif phase=='continue':
        marker=path/PDF_SMOKE_MARKER
        if not marker.is_file():raise ValueError('PDF smoke continuation requires its completed prepare marker')
        value=json.loads(marker.read_text(encoding='utf-8'))
        if value.get('format')!='solo-pdf-world-smoke-v1' or not value.get('campaign_id') or len(value.get('nat20_sha256',''))!=64:
            raise ValueError('Invalid PDF smoke continuation marker')
    else:raise ValueError('Unknown PDF smoke phase')
    return path

def pdf_world_smoke(client,window,app):
    """Short opt-in frozen host probe; no PDF/AI/legacy journey or player profile."""
    assert app.state.offline_mock
    packets=len(getattr(app.state.game.llm,'packets',[]))
    status=client.get('/api/prototype/status');status.raise_for_status()
    assert set(status.json()['components'].values())=={'registered'}
    for name,markers in [('creation.js',('world-readiness','confirm-world')),('prototype.js',('hero_confirmed','rules_confirmed'))]:
        response=client.get('/static/'+name);response.raise_for_status()
        assert all(marker in response.text for marker in markers)
    draft=client.post('/api/creation/drafts');draft.raise_for_status();draft=draft.json()
    url='/api/creation/drafts/'+draft['id'];before=app.state.preparation.get(draft['id'])
    ready=client.get(url+'/world-readiness');ready.raise_for_status()
    assert not ready.json()['ready'] and ready.json()['scope']=='world'
    assert 'quote' not in json.dumps(ready.json()) and 'body' not in ready.json()
    denied=client.post(url+'/confirm-world',json=dict(expected_revision=draft['revision'],run_id='absent',confirm=False))
    assert denied.status_code==409 and app.state.preparation.get(draft['id'])==before
    options=client.get('/api/prototype/options');options.raise_for_status();options=options.json()
    build=dict(options['default_build'],class_slug='rogue',species_slug='human')
    created=client.post('/api/prototype/campaigns',json=dict(name='EXE smoke · Empty world',
        request_id='native-empty',build=build,adventure='sandbox',encounter_monsters=[]))
    created.raise_for_status();cid=created.json()['id']
    native=app.state.store.campaign(cid)['state']['nat20']
    assert native['definition']['encounters']=={} and native['combat'] is None
    screen=client.get('/api/campaigns/'+cid+'/screen');screen.raise_for_status()
    assert not any(row['parameters']['kind']=='start_combat' for row in screen.json()['commands'])
    # The existing explicit offline QA hook also verifies the packaged native binding.
    bound=client.post('/api/prototype/campaigns',json=dict(name='EXE smoke · Native wolf',
        request_id='native-wolf',build=build,adventure='sandbox',encounter_monsters=['wolf']))
    bound.raise_for_status()
    actor=app.state.store.campaign(bound.json()['id'])['state']['nat20']['definition']['encounters']['opening']['actors'][0]
    assert actor['attack_bonus']==4 and actor['attack_bonus_source']['kind']=='native_derived'
    detail=client.get('/api/prototype/catalog/monsters/wolf');detail.raise_for_status()
    profile=detail.json()['attack_profile']
    assert profile['status']=='native_derived' and profile['attack_bonus']==4
    assert profile['monster_sha256'] in actor['attack_bonus_source']['note']
    dom=window.evaluate_js("JSON.stringify({title:document.title,creation:typeof window.soloOpenCreation==='function',fonts:document.fonts.check('16px ChronicleUI')})")
    dom=json.loads(dom) if isinstance(dom,str) else dom
    assert dom['creation'] and dom['fonts']
    assert len(getattr(app.state.game.llm,'packets',[]))==packets
    return dict(new_routes=True,confirm_refused_without_consent=True,resources=True,
        zero_encounter_native=True,native_attack_binding=True,renderer=dom,real_pdf_prepared=False,paid_calls=0)


def pdf_prepared_smoke(client,window,app,directory,phase):
    """Explicit two-process QA through registered APIs and a closed offline transport."""
    from scripts.smoke_fixture import smoke_pdf_bytes
    from .nat20_api import Nat20GameEngine
    model=app.state.game.llm
    assert app.state.offline_mock and getattr(model,'pdf_world_smoke',False)
    path=Path(directory);marker=path/PDF_SMOKE_MARKER
    def api(method,url,**kwargs):
        response=client.request(method,url,**kwargs);response.raise_for_status();return response.json()
    def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    def wait(url,done):
        deadline=time.monotonic()+40
        while True:
            value=api('GET',url)
            if value['status']==done:return value
            if value['status'] in ('failed','error','cancelled','partial'):
                raise AssertionError('Synthetic preparation failed: '+json.dumps(value,ensure_ascii=False))
            if time.monotonic()>deadline:raise TimeoutError('Synthetic preparation timeout: '+url)
            time.sleep(.05)
    def turn(cid,key,text):
        version=app.state.store.campaign(cid)['version']
        return api('POST','/api/campaigns/'+cid+'/command-turn',json=dict(request_id=key,text=text,expected_version=version))
    if phase=='prepare':
        assert not marker.exists() and not app.state.store.list_campaigns()
        draft=api('POST','/api/creation/drafts');did=draft['id'];url='/api/creation/drafts/'+did
        api('PUT',url,json=dict(description='EXE smoke synthetic PDF; D&D 2014 source, separate explicit Native 2024 mechanics.',revision=draft['revision']))
        uploaded=api('POST',url+'/files',files={'file':('synthetic-forge.pdf',smoke_pdf_bytes(),'application/pdf')})
        wait('/api/adventure/jobs/'+uploaded['files'][0]['job']['id'],'complete')
        request=dict(request_id='pdf-smoke-prepare',ruleset='dnd2014',world_structure=True,consent_to_send=True)
        denied=client.post(url+'/prepare',json={**request,'consent_to_send':False})
        assert denied.status_code==409 and not model.pdf_smoke_packets
        started=api('POST',url+'/prepare',json=request)
        status=wait('/api/creation/runs/'+started['id'],'ready')
        assert status['ready'] and status['preparation_mode']=='world_structure' and status['source_ruleset']=='dnd2014'
        ready=api('GET',url+'/world-readiness')
        assert ready['world_ready'] and not ready['ready'] and 'body' not in ready and 'quote' not in json.dumps(ready)
        confirmed=api('POST',url+'/confirm-world',json=dict(expected_revision=ready['revision'],run_id=ready['run_id'],confirm=True))
        assert confirmed['world_readiness']['ready'] and 'body' not in confirmed
        options=api('GET','/api/prototype/options')
        build=dict(options['default_build'],class_slug='rogue',species_slug='human')
        api('POST','/api/prototype/build/preview',json=dict(build=build,prepared_spells=[]))
        created=api('POST','/api/prototype/campaigns',json=dict(name='EXE smoke · PDF герой',
            request_id='pdf-smoke-native',build=build,adventure='prepared:'+did,
            adventure_revision=confirmed['revision'],encounter_monsters=[],mechanics_ruleset='dnd2024',
            rules_confirmed=True,hero_confirmed=True,background_confirmed=True,hero_background='Ищет безопасную дорогу.'))
        cid=created['id'];current=app.state.store.campaign(cid)
        assert current['state']['preparation']['source_documents']
        assert current['state']['preparation']['source_ruleset']=='dnd2014'
        assert current['state']['preparation']['mechanics_ruleset']=='dnd2024'
        # A prepared launch seeds the engine's own verified fight, so the
        # question is whether it is a real registered encounter -- not whether
        # the dict is empty. See tests/test_default_encounter_at_launch.py.
        encounters=current['state']['nat20']['definition']['encounters']
        assert encounters, 'a launched table must be able to fight'
        for fight in encounters.values():
            assert fight['actors']
        for key,text,expected in [('pdf-look','Осматриваюсь','observation'),
                ('pdf-talk','Спрашиваю Dorn про bridge','conversation'),('pdf-check','Проверяю ловкость','resolved')]:
            result=turn(cid,key,text);assert result['status']==expected,result
        saved=api('POST','/api/campaigns/'+cid+'/saves',json=dict(name='EXE smoke · после PDF'))
        current=app.state.store.campaign(cid)
        record=dict(format='solo-pdf-world-smoke-v1',campaign_id=cid,draft_id=did,run_id=started['id'],
            save_id=saved['id'],version=current['version'],nat20_sha256=digest(current['state']['nat20']),
            snapshot=api('GET','/api/campaigns/'+cid+'/screen')['snapshot'])
        marker.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    else:
        record=json.loads(marker.read_text(encoding='utf-8'));cid=record['campaign_id']
        current=app.state.store.campaign(cid)
        assert current['version']==record['version'] and digest(current['state']['nat20'])==record['nat20_sha256']
        assert Nat20GameEngine(app.state.store).restore(cid)['runtime']==current['state']['nat20']
        assert api('GET','/api/campaigns/'+cid+'/screen')['snapshot']==record['snapshot']
        assert any(row['id']==record['save_id'] for row in api('GET','/api/campaigns/'+cid)['saves'])
        result=turn(cid,'pdf-continued-check','Проверяю wis');assert result['status']=='resolved',result
        assert app.state.store.campaign(cid)['version']==record['version']+1
    assert len(app.state.store.list_campaigns())==1
    assert app.state.store.memory.audit()==dict(dangling_events=[],foreign_key_errors=[])
    packets=model.pdf_smoke_packets
    prepared=sum(p.get('kind')=='preparation_world' or p.get('task') in ('campaign-prepare-v1','source-extract-v1') for p in packets)
    assert prepared==(1 if phase=='prepare' else 0),prepared
    dom=window.evaluate_js("JSON.stringify({title:document.title,creation:typeof window.soloOpenCreation==='function',fonts:document.fonts.check('16px ChronicleUI')})")
    dom=json.loads(dom) if isinstance(dom,str) else dom
    assert dom['creation'] and dom['fonts']
    return dict(phase=phase,synthetic_pdf=True,real_pdf_prepared=phase=='prepare',
        source_ruleset='dnd2014',mechanics_ruleset='dnd2024',world_confirmed=True,native_hero=True,
        game_and_save=True,new_process_continued=phase=='continue',version=app.state.store.campaign(cid)['version'],
        preparation_mock_requests=prepared,mock_transport_requests=len(packets),real_provider_calls=0,
        paid_calls=0,renderer=dom)

def navigation_smoke(client,window):
    assert getattr(window,'_solo_navigation',None),'Native navigation delegates were not attached'
    def read(expression):
        value=window.evaluate_js('JSON.stringify('+expression+')')
        return json.loads(value) if isinstance(value,str) else value
    def until(expression):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            value=read(expression)
            if value:return value
            time.sleep(.05)
        raise AssertionError('Native navigation condition did not complete: '+expression)
    until('!!window.soloNavigation')
    window.evaluate_js('(window.soloOpenLegacyPreparation||window.soloOpenPreparation)();true')
    until("!!document.querySelector('#preparation.active input[aria-label=\"Название хроники\"]')")
    window.evaluate_js("const name=document.querySelector('#preparation input[aria-label=\"Название хроники\"]');name.value='EXE smoke · Навигация';name.dispatchEvent(new Event('input'));true")
    hidden=read("getComputedStyle(document.querySelector('#preparation a[href=\"/static/preparation-guide.md\"]')).display==='none'")
    assert hidden,'Developer guide is visible in normal preparation'
    window.evaluate_js("window.open('/static/preparation-guide.md','_blank');true")
    until("document.getElementById('document-viewer').open&&document.querySelector('.document-text').textContent.includes('Подготовка источника')")
    assert read("location.pathname==='/'"),'A target=_blank guide replaced the main WebView'
    window.evaluate_js("document.querySelector('#document-viewer .document-toolbar button').click();true")
    assert until("!document.getElementById('document-viewer').open")
    assert read("document.querySelector('#preparation input[aria-label=\"Название хроники\"]').value==='EXE smoke · Навигация'")
    for id in ('asset-attribution','player-help'):
        window.evaluate_js('window.soloNavigation.openDocument('+json.dumps(id)+');true')
        until("document.getElementById('document-viewer').open&&document.querySelector('.document-text').textContent.length>50")
        window.evaluate_js("document.querySelector('#document-viewer .document-toolbar button').click();true")
        until("!document.getElementById('document-viewer').open")
    window.evaluate_js("location.href='http://127.0.0.1:1025/forbidden';true")
    assert until("location.pathname==='/'&&document.getElementById('preparation').classList.contains('active')")
    # The cookie-authenticated API and ordinary asset fetches are not navigation.
    assert client.get('/api/campaigns').status_code==200 and client.get('/static/play.js').status_code==200
    return dict(native_new_window_intercepted=True,normal_guide_hidden=True,document_mouse_close_control=True,draft_value_preserved=True,markdown_and_text_viewer=True,other_loopback_process_blocked=True,backend_and_assets_available=True)

def tabletop_smoke(client):
    from .interactions import SceneObject
    from .inventory import new_item
    campaign=client.post('/api/campaigns',json={'name':'EXE smoke · Tabletop B','hero':{'name':'Лира','hp':2},'ruleset':'dnd2014','map_travel':False}).json()['id'];base='/api/campaigns/'+campaign
    objects=[SceneObject(id='exe-door',name='дверь',kind='door',lock='unlocked',destination='Кладовая B').model_dump(),SceneObject(id='exe-locked',name='железная дверь',aliases=['железную дверь'],kind='door',lock='locked').model_dump(),SceneObject(id='exe-potion',name='лечебное зелье',kind='item',item=new_item('healing_potion',1,'scene','EXE smoke fixture')).model_dump()]
    package=dict(name='EXE B fixture',source='Synthetic registered declarations',locations=[dict(name='Комната B',aliases=['комнату b'],known_to_hero=True,description='Комната.',interactions=objects),dict(name='Кладовая B',aliases=['кладовую b'],known_to_hero=True,description='Полки.')],nodes=[dict(id='exe-room',name='Комната B',coordinates=[1,1]),dict(id='exe-storage',name='Кладовая B',coordinates=[1,2])],routes=[{'from':'tavern','to':'exe-room','meters':1,'minutes':1,'description':'проход'},{'from':'exe-room','to':'exe-storage','meters':1,'minutes':1,'description':'дверь'}])
    client.post(base+'/world/import',json={'request_id':'fixture','confirm':True,'package':package}).raise_for_status()
    def play(key,text):
        response=client.post(base+'/play',json={'request_id':key,'text':text});response.raise_for_status();result=response.json();assert result['kind']=='event',result;return result['event']
    play('room','Иду в комнату B');assert play('locked','Открываю железную дверь')['results'][0]['status']=='impossible'
    assert play('examine','Осматриваю дверь')['results'][0]['action']=='examine'
    assert play('open','Открываю дверь')['results'][0]['action']=='open'
    pick=play('pick','Поднимаю лечебное зелье');assert play('pick','Поднимаю лечебное зелье')['id']==pick['id']
    play('use','Использую лечебное зелье');play('move','Иду в кладовую B')
    state=client.get(base).json();assert state['state']['location']=='Кладовая B' and state['state']['hero']['hp']>2
    hero=state['state']['hero'];assert client.put(base+'/character',json={'request_id':'denied-edit','hero':hero}).status_code==403
    client.post('/api/advanced/session',json={'confirm':True}).raise_for_status();client.put(base+'/character',json={'request_id':'advanced-edit','hero':hero}).raise_for_status();client.delete('/api/advanced/session').raise_for_status()
    note={'request_id':'note','text':'Личная догадка EXE','expected_revision':0};assert client.put(base+'/notes',json=note).json()['revision']==1
    assert client.put(base+'/notes',json=note).json()['revision']==1
    body={'request_id':'free-roll','sides':20};assert client.post(base+'/dice',json=body).json()==client.post(base+'/dice',json=body).json()
    return dict(mapless_six_interactions=True,request_replay_without_duplicate_pickup=True,advanced_server_gate=True,notes_idempotent=True,dice_idempotent=True,edition='2014',paid_calls=0)

def adventure_smoke(client,campaign):
    import io,zipfile
    from pypdf import PdfWriter
    pdf=PdfWriter();pdf.add_blank_page(300,200);raw=io.BytesIO();pdf.write(raw);archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('карты/проверка.pdf',raw.getvalue())
    bundle=client.post('/api/adventure/library',json={'title':'EXE smoke · Adventure','metadata':{'rights':'synthetic'}}).json()['id']
    response=client.post('/api/adventure/library/'+bundle+'/files',files={'file':('карты.zip',archive.getvalue(),'application/zip')},data={'purpose':'maps'});response.raise_for_status();job=response.json()['id'];deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        status=client.get('/api/adventure/jobs/'+job).json()['status']
        if status in ('complete','failed','cancelled'):break
        time.sleep(.1)
    assert status=='complete',status
    client.post('/api/adventure/review/session',json={'confirm_spoilers':True}).raise_for_status()
    review=client.get('/api/adventure/review/library/'+bundle).json();material=review['materials'][0]
    private=client.get('/api/adventure/review/materials/'+material['id']+'?width=1000');private.raise_for_status();assert private.content.startswith(b'\x89PNG')
    client.put('/api/adventure/review/materials/'+material['id'],json={'scope':'player','name':'Проверенная карта','confirmed':True,'sanitized':True}).raise_for_status()
    state=client.get('/api/campaigns/'+campaign).json()['state'];preset=state['ruleset'];start=state.get('elapsed_minutes',0)
    definition={'id':'exe-map','title':'Synthetic reviewed map','ruleset':preset['id'],'ruleset_version':preset['version'],'media':[material['id']],'events':[{'id':'find','category':'timed','after_minutes':10,'source':{'document':material['document'],'page':1,'review':'confirmed'},'effects':[{'kind':'handout','target':material['id']}]}]}
    client.put('/api/adventure/review/library/'+bundle+'/definition',json={'definition':definition,'confirmed':True}).raise_for_status()
    client.post('/api/adventure/review/campaigns/'+campaign+'/attach',json={'bundle':bundle,'request_id':'exe-attach','confirm':True}).raise_for_status()
    client.delete('/api/adventure/review/session').raise_for_status()
    assert client.get('/api/adventure/review/materials/'+material['id']).status_code==403
    url='/api/campaigns/'+campaign+'/adventure/materials/'+material['id'];assert client.get(url).status_code==404
    response=client.post('/api/campaigns/'+campaign+'/play',json={'request_id':'exe-adventure-wait','text':'Жду десять минут.'});response.raise_for_status()
    assert client.get(url).status_code==200
    after=client.get('/api/campaigns/'+campaign).json();assert after['state']['elapsed_minutes']==start+10
    saved=client.post('/api/campaigns/'+campaign+'/saves',json={'name':'EXE Adventure'});saved.raise_for_status()
    client.post('/api/campaigns/'+campaign+'/undo').raise_for_status();assert client.get(url).status_code==404
    from .ai_roles import prompt
    assert all(prompt(n) for n in ('qwen_interpreter_v1','deepseek_narrator_v1','venice_narrator_v1','gemini_pdf_analyst_v1'))
    return dict(frozen_zip_worker=True,unicode_archive=True,pdfium_render=True,player_acl=True,review_session_revoked=True,timed_reveal=True,save=True,undo=True,role_prompts_bundled=True,inference_calls=0)
def obsidian_smoke(client,window,app):
    registry=client.get('/api/visual-assets');registry.raise_for_status();assets=registry.json()['assets'];assert len(assets)==65
    total=0
    for asset in assets:
        response=client.get('/static/'+asset['path']);response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest()==asset['sha256'];total+=len(response.content)
        assert client.get('/static/'+asset['thumbnail']).status_code==200
    assert client.get('/static/visual/ATTRIBUTION.md').status_code==200
    draft=client.post('/api/preparation/drafts').json();id=draft['id'];body=draft['body']
    source=b'## Start\nThis source contains actual text for provenance and offline indexing. No provider calls are needed.'
    imported=client.post('/api/preparation/drafts/'+id+'/sources',files={'file':('source.txt',source,'text/plain')});imported.raise_for_status();draft=imported.json();body=draft['body'];options=client.get('/api/preparation/options').json()
    body.update(name='EXE smoke · Obsidian',opening='Лесная дорога у перекрёстка. Ветер шевелит траву.',prepared=True,background_confirmed=True,hero={**options['hero'],'name':'Лира','background':'Ищет пропавшего гонца.','languages':['Общий']},equipment=['rope','longsword'])
    saved=client.put('/api/preparation/drafts/'+id,json={'body':body,'revision':draft['revision']});saved.raise_for_status()
    created=client.post('/api/preparation/drafts/'+id+'/launch',json={'confirm':True});created.raise_for_status();cid=created.json()['id']
    assert client.post('/api/preparation/drafts/'+id+'/launch',json={'confirm':True}).json()['id']==cid
    state=client.get('/api/campaigns/'+cid).json();item=next(i for i in state['state']['economy']['items'] if i['catalog']=='longsword')
    equipped=client.post('/api/campaigns/'+cid+'/play',json={'request_id':'obsidian-equip','text':'Мастер, я беру в руки выбранный предмет.','selection':{'kind':'item','id':item['id']}});equipped.raise_for_status();assert equipped.json()['kind']=='event'
    sheet=client.get('/api/campaigns/'+cid+'/character').json();assert sheet['hero']['languages']==['Общий']
    adventure=adventure_smoke(client,cid);tabletop=tabletop_smoke(client)
    # The native renderer must actually load the UI and local fonts, without an Internet asset origin.
    deadline=time.monotonic()+12;dom=None
    while time.monotonic()<deadline:
        dom=window.evaluate_js("JSON.stringify({ready:!!window.soloOpenPreparation&&!!window.soloInspect&&document.querySelectorAll('.tabletop-primary .nav').length===3,primary:[...document.querySelectorAll('.tabletop-primary .nav')].map(b=>b.textContent.trim()),fonts:document.fonts.check('16px ChronicleUI')&&document.fonts.check('18px ChronicleBook'),resources:performance.getEntriesByType('resource').map(r=>r.name),hasExit:!!document.querySelector('[data-view=exit]'),dice:!!document.getElementById('tabletop-dice'),notes:!!document.getElementById('player-notes')})")
        if isinstance(dom,str):dom=json.loads(dom)
        if dom and dom.get('ready') and dom.get('fonts'):break
        time.sleep(.1)
    assert dom and dom['ready'] and dom['fonts'] and dom['hasExit'],dom
    assert dom['primary']==['Игра','Персонаж','Блокнот'] and dom['dice'] and dom['notes'],dom
    navigation=navigation_smoke(client,window)
    assert all(url.startswith(str(client.base_url)) for url in dom['resources']),dom['resources']
    scales=[]
    # Native application UI scale; global Windows display preferences are not changed.
    for percent in (100,125,150):
        script="const s=document.getElementById('ui-scale');s.value="+str(percent)+";s.dispatchEvent(new Event('input'));JSON.stringify({scale:document.body.getBoundingClientRect().width,overflow:document.documentElement.scrollWidth>innerWidth});"
        result=window.evaluate_js(script)
        if isinstance(result,str):result=json.loads(result)
        assert result and not result['overflow'],result
        scales.append(percent)
    window.evaluate_js("document.getElementById('ui-scale').value=100;document.getElementById('ui-scale').dispatchEvent(new Event('input'));true")
    from scripts.desktop_cloud_smoke import cloud_smoke
    cloud=cloud_smoke(client,window,app)
    from scripts.desktop_creation_smoke import creation_smoke
    creation=creation_smoke(client,window)
    # Exercise the real UI handler with the native session cookie, then graceful host shutdown.
    window.evaluate_js("document.querySelector('[data-view=exit]').click(); true")
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        if client.get('/api/desktop/status').json()['exit_requested']:break
        time.sleep(.01)
    else:raise AssertionError('Exit UI did not reach the protected desktop handler')
    return dict(cloud=cloud,creation=creation,navigation=navigation,tabletop=tabletop,primary_navigation=dom['primary'],adventure=adventure,local_assets=len(assets),asset_bytes=total,thumbnails_verified=True,asset_checksums=True,native_fonts_ready=dom['fonts'],native_ui_loaded=dom['ready'],native_ui_scale_tests=scales,windows_display_dpi_changed=False,no_external_asset_origins=True,prepared_campaign_idempotent=True,selected_item_through_dialogue=True,exit_ui_clicked=True,exit_handler_verified=True,source_index_offline=True)
