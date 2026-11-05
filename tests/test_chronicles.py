from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from solo.preparation import Draft
from test_preparation_pipeline import new,terminal


def prepared(client):
    value,url=new(client)
    run=client.post(url+'/prepare',json=dict(request_id='prepare',ruleset='dnd2014',consent_to_send=True)).json()
    terminal(client,run['id'])
    return value['id'],url
from test_preparation_pipeline import PreparationFixture

def test_player_library_shows_old_campaign_and_new_draft_without_private_world(tmp_path):
    with TestClient(create_app(tmp_path/'solo.sqlite3',PreparationFixture())) as client:
        store=client.app.state.store
        campaign=store.create('Прежняя история',Hero(name='Лира',background='Ищу пропавшего гонца.'))
        with store.connection(True) as db:
            import json
            state=campaign['state'];state['private_diagnostic']='GM_SECRET_CHRONICLE_992'
            db.execute('UPDATE campaigns SET state=? WHERE id=?',(json.dumps(state,ensure_ascii=False),campaign['id']))
        adventure,url=prepared(client)
        result=client.get('/api/chronicles');result.raise_for_status();data=result.json()
        assert 'GM_SECRET_CHRONICLE' not in result.text
        old=data['campaigns'][0];assert old['hero']=='Лира' and old['description']=='Ищу пропавшего гонца.' and old['scene']
        # Черновиков может быть несколько: ищем именно подготовленный.
        draft=next(d for d in data['drafts'] if d['id']==adventure);assert draft['id']==adventure and draft['status']=='ready' and draft['description']
        # Прежний редактор отключён (единый путь): его маршрут обязан отвечать
        # отказом, а не создавать героя. Проверяем именно это.
        assert client.post(url+'/character').status_code==404
        assert not client.get('/api/chronicles').json()['drafts'][0].get('character_mode')

def test_created_pc_sheet_uses_human_labels_and_library_hides_launched_draft(tmp_path):
    # One gameplay path: the sheet is produced by the Native hero wizard, because the
    # legacy confirm no longer creates a campaign (solo/character_creation.py).
    from test_preparation_pipeline import terminal
    from nat20_builds import legal
    with TestClient(create_app(tmp_path/'solo.sqlite3',PreparationFixture(),experimental=True)) as client:
        adventure,url=prepared(client)
        run=client.post(url+'/prepare',json=dict(request_id='native',consent_to_send=True,ruleset='dnd2024')).json()['id']
        terminal(client,run)
        world=client.get(url+'/world-readiness').json()
        client.post(url+'/confirm-world',json=dict(expected_revision=world['revision'],run_id=run,confirm=True))
        # The prepared id is the draft id itself; the option list is only a catalogue.
        option=dict(id='prepared:'+adventure,revision=client.app.state.preparation.get(adventure)['revision'])
        build=dict(legal(class_slug='fighter',species_slug='human',background='soldier'));build['equipment']=['longsword']
        campaign=client.post('/api/prototype/campaigns',json=dict(name='Арден',adventure=option['id'],
            adventure_revision=option['revision'],build=build,prepared_spells=[],encounter_monsters=[],
            encounter_attack_bonuses={},encounter_attack_sources={},mechanics_ruleset='dnd2024',
            rules_confirmed=True,hero_confirmed=True,background_confirmed=True,hero_background='soldier',
            request_id='confirm'));campaign.raise_for_status()
        assert 'nat20' in client.app.state.store.campaign(campaign.json()['id'])['state']
        sheet=client.get('/api/campaigns/'+campaign.json()['id']+'/character').json()
        # The Native sheet names every feature in Russian; Nat20's own catalogue is English.
        names={f['id']:f['name'] for f in sheet['features']}
        assert names['fighting-style']=='Боевой стиль' and names['second-wind']=='Второе дыхание'
        assert names['weapon-mastery']=='Мастерство оружия' and names['resourceful']=='Находчивость'
        assert not any(n.isascii() for n in names.values()), names
        assert sheet['identity']['classes']=={'Воин':1} and sheet['hero']['hp']['maximum']>0
        library=client.get('/api/chronicles').json();assert not library['drafts'] and library['campaigns'][0]['hero']=='Арден'
