from fastapi.testclient import TestClient
from solo.app import create_app
from solo.contracts import Hero
from solo.inventory_service import InventoryCommand,InventoryService
from test_inventory import setup,grant

def test_stale_manual_sheet_cannot_overwrite_new_equipment(tmp_path):
    store,cid,service,mid=setup(tmp_path,hero=Hero(name='Герой',armor_proficiencies=['light']))
    with TestClient(create_app(store.path)) as client:
        old=client.get('/api/campaigns/'+cid+'/character').json()
        armor=grant(service,cid,'leather')
        service.execute(cid,InventoryCommand(request_id='equip',kind='equip',item=armor,slot='armor'))
        assert client.post('/api/advanced/session',json={'confirm':True}).status_code==200
        response=client.put('/api/campaigns/'+cid+'/character',json=dict(request_id='stale-sheet',hero=old['hero'],expected_version=old['version']))
        assert response.status_code==409 and store.campaign(cid)['state']['hero']['ac']==13
        fresh=client.get('/api/campaigns/'+cid+'/character').json();fresh['hero']['armor_proficiencies']=[]
        assert client.post('/api/advanced/session',json={'confirm':True}).status_code==200
        response=client.put('/api/campaigns/'+cid+'/character',json=dict(request_id='remove-prof',hero=fresh['hero'],expected_version=fresh['version']))
        assert response.status_code==409

def test_explicit_text_grants_wallet_and_merchant_share_journal(tmp_path):
    from solo.storage import Store
    store=Store(tmp_path/'text-items.sqlite3');cid=store.create('Текст',Hero(name='Герой'))['id']
    with TestClient(create_app(store.path)) as client:
        texts=['Подтверждаю кошелёк: 1000 CP; источник: стартовый кошелёк игрока','Подтверждаю получение: дубинка; количество: 1; источник: стартовое снаряжение игрока','Подтверждаю торговца: {"name":"Ирина","stock":[{"catalog":"rope","quantity":2}]}']
        for index,text in enumerate(texts):
            body=dict(request_id='text-'+str(index),text=text)
            first=client.post('/api/campaigns/'+cid+'/play',json=body)
            assert first.status_code==200 and first.json()['kind']=='event'
            repeated=client.post('/api/campaigns/'+cid+'/play',json=body)
            assert repeated.json()['event']['id']==first.json()['event']['id']
        data=client.get('/api/campaigns/'+cid+'/inventory').json()
        assert data['wallet_cp']==1000 and data['items'][0]['catalog']=='club' and data['merchants'][0]['name']=='Ирина'
