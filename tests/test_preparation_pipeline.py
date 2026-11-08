import asyncio,copy,json,time
from contextlib import nullcontext
import pytest
from fastapi.testclient import TestClient
from solo.app import create_app
from solo.llm import MockLLM
from solo.ai_roles import routerai_preset
from solo.prepared_world import PreparedWorldValidator

def original():
    return dict(title='Северный караван',ruleset='dnd2014',start='Перекрёсток',entities=[
        dict(key='crossroads',kind='location',origin='original',data=dict(name='Перекрёсток',description='Снег укрывает дорогу. У столба ждёт проводница.')),
        dict(key='tower',kind='location',origin='original',data=dict(name='Башня',description='Старая башня над рекой.',secrets=['GM_SECRET_389'])) ,
        dict(key='guide',kind='npc',location='Перекрёсток',origin='original',data=dict(name='Ирина',character='Проводница в плаще.',motivation='GM_NPC_SECRET_778')),
        dict(key='road',kind='route',origin='original',data={'from':'Перекрёсток','to':'Башня','meters':1000,'minutes':20,'description':'Дорога на север','explorable':True}),
        dict(key='later',kind='event',origin='original',data=dict(id='later',category='timed',after_minutes=20,effects=[dict(kind='notice',text='Приближается буря.',public=True)]))])

class PreparationFixture(MockLLM):
    def __init__(self,result=None,slow=False):super().__init__();self.result=result;self.slow=slow;self.payloads=[]
    def configured(self):return routerai_preset(),'fixture-only-never-sent'
    def scope(self,*args):return nullcontext()
    def contract_error(self,code):self.metrics.append(dict(status='invalid_contract',error=code))
    async def stream(self,system,user,**kwargs):
        assert kwargs['mode']=='preparation_world';payload=json.loads(user);self.payloads.append(payload)
        self.metrics.append(dict(mode='preparation_world',mock=True))
        if self.slow:await asyncio.sleep(10 if self.slow is True else self.slow)
        value=self.result(payload) if callable(self.result) else self.result or original()
        raw=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        for offset in range(0,len(raw),31):yield raw[offset:offset+31];await asyncio.sleep(0)

def terminal(client,id):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        value=client.get('/api/creation/runs/'+id).json()
        if value['status'] not in ('queued','running'):return value
        time.sleep(.03)
    raise AssertionError(value)

def new(client,text='Мрачный север, караван, D&D 2014'):
    draft=client.post('/api/creation/drafts').json();url='/api/creation/drafts/'+draft['id']
    return client.put(url,json={'description':text,'revision':draft['revision']}).json(),url

def test_original_campaign_builds_real_world_events_not_only_index_and_is_private(tmp_path):
    llm=PreparationFixture()
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client);request=dict(request_id='first',consent_to_send=True,ruleset='dnd2014')
        started=client.post(url+'/prepare',json=request);assert started.status_code==200,started.text
        status=terminal(client,started.json()['id']);assert status['ready'],status
        assert status['summary']['locations']==2 and status['summary']['npc']==1
        assert 'GM_SECRET' not in json.dumps(status) and 'GM_NPC_SECRET' not in client.get(url).text
        with client.app.state.preparation.connection() as db:
            trace=db.execute('SELECT body FROM creation_batch_history WHERE run=?',(status['id'],)).fetchone()[0]
        assert 'GM_NPC_SECRET_778' in trace # reproducible privately, never in the player projection
        private=client.app.state.preparation_pipeline.raw(status['id']);result=json.loads(private['result'])
        stored=client.app.state.adventures.definition(client.app.state.creation.owned(draft['id'])['body']['adventure_bundle'])
        assert stored.world.routes[0].from_=='p0' # route aliases survive private persistence/reload
        assert len(result['definition']['events'])==1 and result['definition']['world']['routes']
        assert result['definition']['world']['locations'][0]['npc'][0]['private_knowledge']==['GM_NPC_SECRET_778']
        assert client.post(url+'/prepare',json=request).json()['id']==status['id'] and len(llm.payloads)==1
        assert client.get('/api/campaigns').json()==[] # creator confirmation still required
        latest=client.get(url).json();client.put(url,json=dict(description='Другой замысел',revision=latest['revision'])).raise_for_status()
        assert client.get(url).json()['preparation']['status']=='stale'
        assert client.post(url+'/prepare',json=request).status_code==409
    with TestClient(create_app(tmp_path/'solo.sqlite3',PreparationFixture())) as client:
        assert client.get('/api/creation/runs/'+status['id']).json()['status']=='ready'

def test_consent_edition_and_cancel_do_not_mutate_campaign_or_sources(tmp_path):
    llm=PreparationFixture(slow=True)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'История о караване')
        assert client.post(url+'/prepare',json=dict(request_id='missing-edition',consent_to_send=True)).status_code==409
        assert client.post(url+'/prepare',json=dict(request_id='no-consent',ruleset='dnd2014')).status_code==409
        assert not llm.payloads
        started=client.post(url+'/prepare',json=dict(request_id='cancel',ruleset='dnd2014',consent_to_send=True)).json()
        client.post('/api/creation/runs/'+started['id']+'/cancel').raise_for_status()
        assert terminal(client,started['id'])['status']=='cancelled'
        assert not client.app.state.creation.owned(draft['id'])['body']['prepared']
        assert client.get('/api/campaigns').json()==[]
        llm.slow=False
        retry=client.post(url+'/prepare',json=dict(request_id='retry',ruleset='dnd2014',consent_to_send=True)).json()
        assert terminal(client,retry['id'])['ready']

def source_result(payload):
    import re
    page=payload['pages'][0];span=re.match(r'\[(S\d+)\]',page['text'])
    proof=dict(source_span=span[1]) if span else dict(document=page['document'],page=page['page'],quote=page['text'])
    return dict(title='Книга',ruleset='dnd2014',start='Мост',entities=[
        dict(key='bridge',kind='location',data=dict(name='Мост',description='Перед вами мост.',secrets=['GM_BOOK_SECRET_991']),evidence=[proof]),
        dict(key='tower',kind='location',data=dict(name='Башня',description='Башня скрывает логово.'),evidence=[proof]),
        dict(key='fake',kind='location',data=dict(name='Выдуманный город'),evidence=[dict(document=page['document'],page=page['page'],quote='несуществующая цитата')],essential=False)])

def test_source_text_and_player_request_are_both_sent_quotes_checked_and_unsupported_quarantined(tmp_path):
    llm=PreparationFixture(source_result)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client,'По книге, мрачный север, D&D 2014')
        source='Прочитайте игрокам:\nПеред вами мост.\n\nМост. Башня. Башня скрывает логово. GM_BOOK_SECRET_991'
        uploaded=client.post(url+'/files',files={'file':('book.txt',source.encode())});uploaded.raise_for_status();job=uploaded.json()['files'][0]['job']['id']
        deadline=time.monotonic()+15
        while client.get('/api/adventure/jobs/'+job).json()['status']!='complete':
            assert time.monotonic()<deadline;time.sleep(.03)
        started=client.post(url+'/prepare',json=dict(request_id='book',ruleset='dnd2014',consent_to_send=True)).json();status=terminal(client,started['id']);assert status['ready'],status
        result=json.loads(client.app.state.preparation_pipeline.raw(status['id'])['result'])
        assert result['quarantine'][0]['key']=='fake'
        places=result['definition']['world']['locations'];assert places[0]['description']=='Перед вами мост.' and not places[1]['description']
        assert llm.payloads[0]['description'].startswith('По книге') and llm.payloads[0]['pages'][0]['text']=='[S001] '+source
        assert 'GM_BOOK_SECRET' not in json.dumps(status) and 'GM_BOOK_SECRET' not in client.get(url).text
        assert result['provenance']['bridge']['evidence'][0]['page']==1

@pytest.mark.parametrize('bad',[lambda p: '{bad JSON',lambda p:{**original(),'entities':original()['entities']+[dict(key='unknown-rule',kind='special_rule',origin='original',data={'name':'Свободное воскрешение'})]}])
def test_invalid_contract_and_critical_unknown_rule_never_mark_ready(tmp_path,bad):
    with TestClient(create_app(tmp_path/'solo.sqlite3',PreparationFixture(bad))) as client:
        draft,url=new(client);response=client.post(url+'/prepare',json=dict(request_id='bad',ruleset='dnd2014',consent_to_send=True));response.raise_for_status()
        status=terminal(client,response.json()['id']);assert status['status'] in ('blocked','failed') and not status['ready']
        assert not client.app.state.creation.owned(draft['id'])['body']['prepared']

def test_edit_during_preparation_never_applies_a_stale_world(tmp_path):
    with TestClient(create_app(tmp_path/'solo.sqlite3',PreparationFixture(slow=.25))) as client:
        draft,url=new(client);started=client.post(url+'/prepare',json=dict(request_id='stale',ruleset='dnd2014',consent_to_send=True)).json()
        changed=client.get(url).json();client.put(url,json=dict(description='Новая история',revision=changed['revision'])).raise_for_status()
        status=terminal(client,started['id']);assert status['status']=='failed' and 'изменились' in status['message']
        assert client.get(url).json()['description']=='Новая история'
        assert not client.app.state.creation.owned(draft['id'])['body']['prepared']

def test_preparation_budget_blocks_before_any_provider_request(tmp_path):
    from decimal import Decimal
    from test_ai_roles import setup
    requests=[]
    llm,profile=setup(tmp_path,lambda r:requests.append(r))
    profile.preparation_budget=Decimal('0');llm.settings.save(profile)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft,url=new(client);started=client.post(url+'/prepare',json=dict(request_id='budget',ruleset='dnd2014',consent_to_send=True)).json()
        status=terminal(client,started['id']);assert status['status']=='failed' and 'Лимит расходов' in status['message']
        assert not requests

@pytest.mark.parametrize('collapsed',[False,True])
def test_read_aloud_presence_does_not_publish_adjacent_gm_prose(tmp_path,collapsed):
    from solo.adventure_library import AdventureLibrary
    library=AdventureLibrary(tmp_path)
    text='Прочитайте игрокам:\nМост. Ирина. Проводница в плаще. Дверь.\n\nИрина знает GM_SECRET_710. Тайник. Уровни 1–5. 4 персонажа.'
    if collapsed:text=text.replace('\n\n','\n') # PDF text extraction can drop visual paragraph spacing
    document='a'*64
    with library.db() as db:
        db.execute('INSERT INTO documents(id,format,bytes,status) VALUES(?,?,?,?)',(document,'txt',len(text),'ready'))
        db.execute('INSERT INTO pages(document,number,label,content) VALUES(?,?,?,?)',(document,1,'1',text))
    proof=dict(document=document,page=1,quote=' '.join(text.split()) if collapsed else text)
    entities=[dict(key='bridge',kind='location',data=dict(name='Мост',description='Мост.'),evidence=[proof]),
        dict(key='guide',kind='npc',location='Мост',data=dict(name='Ирина',character='Проводница в плаще.',private_knowledge=['Ирина знает GM_SECRET_710.']),evidence=[proof]),
        dict(key='door',kind='object',location='Мост',data=dict(id='door',name='Дверь',kind='door',description='Ирина знает GM_SECRET_710.'),evidence=[proof]),
        dict(key='hidden',kind='object',location='Мост',data=dict(id='hidden',name='Тайник',kind='scenery',visible=True),evidence=[proof])]
    batch=dict(title='Мост',start='Мост',ruleset='dnd2014',minimum_level=1,maximum_level=5,party_size=4,metadata_evidence=[proof],entities=entities)
    validator=PreparedWorldValidator(library,[document]);result=validator.compile([batch],'dnd2014')
    assert result['ready'],result['quarantine']
    definition=result['definition'];place=definition['world']['locations'][0]
    assert place['npc'][0]['present'] and place['npc'][0]['character']=='Проводница в плаще.'
    assert place['interactions'][0]['visible'] and place['interactions'][0]['description']==''
    assert not place['interactions'][1]['visible']
    assert (definition['minimum_level'],definition['maximum_level'],definition['recommended_party'])==(1,5,4)
    batch['maximum_level']=8
    assert not validator.compile([batch],'dnd2014')['ready']
    batch['maximum_level']=5
    batch['entities'].append(dict(key='leak',kind='event',evidence=[proof],data=dict(id='leak',category='timed',effects=[dict(kind='notice',text='Ирина знает GM_SECRET_710.',public=True)])))
    leaked=validator.compile([batch],'dnd2014');assert not leaked['ready'] and not leaked['definition']['events']

def test_no_grounded_location_is_blocked_with_private_diagnostics_instead_of_crashing(tmp_path):
    from solo.adventure_library import AdventureLibrary
    value=dict(title='Непроверенный мир',entities=[dict(key='false',kind='location',data=dict(name='Выдумка'),evidence=[])])
    result=PreparedWorldValidator(AdventureLibrary(tmp_path),[]).compile([value],'dnd2014')
    assert not result['ready'] and result['definition']['world'] is None
    assert result['quarantine'][0]['key']=='false'

def test_optional_missing_reference_and_media_cannot_silently_publish(tmp_path):
    from solo.adventure_library import AdventureLibrary
    batch=original();batch['entities'] += [dict(key='optional',kind='object',origin='original',location='Нет такой комнаты',essential=False,data=dict(id='optional',name='Камень',kind='scenery'))]
    validator=PreparedWorldValidator(AdventureLibrary(tmp_path),[],original=True,original_document='authored')
    result=validator.compile([batch],'dnd2014');assert result['ready'] and result['quarantine'][0]['key']=='optional'
    batch['entities'].append(dict(key='unsafe-media',kind='media',origin='original',data=dict(id='file:///secret.png')))
    assert not validator.compile([batch],'dnd2014')['ready']


def test_source_number_cannot_be_a_substring_of_another_magnitude(tmp_path):
    from solo.preparation_contracts import PreparedEntity
    from solo.adventure_library import AdventureLibrary
    validator=PreparedWorldValidator(AdventureLibrary(tmp_path),[])
    entity=PreparedEntity(key='road',kind='route',data={'meters':100})
    with pytest.raises(ValueError,match='Число'):
        validator.grounded(entity,[{'quote':'Длина дороги: 1000 метров.'}])
    validator.grounded(entity,[{'quote':'Длина дороги: 100 метров.'}])

def test_player_path_reads_edition_from_the_book_and_needs_no_mode_choice(tmp_path):
    """A player uploads a book and presses prepare: no edition, no mode choice."""
    def batch(payload):
        import re
        page=payload['pages'][0];span=re.match(r'\[(S\d+)\]',page['text'])
        proof=dict(source_span=span[1]) if span else dict(document=page['document'],page=page['page'],quote=page['text'])
        return dict(title='Книга',start='Мост',entities=[
            dict(key='bridge',kind='location',data=dict(name='Мост',description='Перед вами мост.'),evidence=[proof]),
            dict(key='tower',kind='location',data=dict(name='Башня'),evidence=[proof]),
            dict(key='guard',kind='npc',location='Мост',data=dict(name='Стражник',character='Стоит у моста.'),evidence=[proof]),
            dict(key='rats',kind='opponent',data=dict(name='Крысы'),evidence=[proof])])
    llm=PreparationFixture(batch)
    with TestClient(create_app(tmp_path/'solo.sqlite3',llm)) as client:
        draft=client.post('/api/creation/drafts').json();url='/api/creation/drafts/'+draft['id']
        client.put(url,json={'description':'Книга пятой редакции','revision':draft['revision']}).raise_for_status()
        source=('Прочитайте игрокам:\nПеред вами мост.\n\nМост. Башня. Стражник стоит у моста. Крысы в подвале. '
                'Это приключение для пятой редакции. Уровни 1-3, партия из четырёх персонажей.')
        client.post(url+'/files',files={'file':('book.txt',source.encode())}).raise_for_status()
        job=client.get(url).json()['files'][0]['job']['id']
        deadline=time.monotonic()+20
        while client.get('/api/adventure/jobs/'+job).json()['status']!='complete':
            assert time.monotonic()<deadline;time.sleep(.03)
        # Exactly what a plain upload sends: no ruleset, no world_structure flag.
        response=client.post(url+'/prepare',json=dict(request_id='player',consent_to_send=True))
        assert response.status_code==200,response.text
        status=terminal(client,response.json()['id'])
        detail=json.loads(client.app.state.preparation_pipeline.raw(status['id'])['result'])
        assert status['ready'],json.dumps(dict(message=status['message'],quarantine=detail['quarantine'],ready=detail['ready'],start=detail.get('start')),ensure_ascii=False)
        assert llm.payloads,'the book must actually be sent'

