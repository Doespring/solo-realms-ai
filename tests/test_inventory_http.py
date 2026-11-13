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
