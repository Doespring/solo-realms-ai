"""Local UI smoke with a disposable profile, shared screen fixture and no remote AI.

Run from project root: .venv/Scripts/python.exe -B scripts/smoke_play_screen.py
Only the isolated smoke server exposes /__smoke__; production never does.
"""
import os,sys,json,argparse,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ['SOLO_TEST']='1'
from fastapi.responses import JSONResponse
from solo.app import create_app
from solo.contracts import Hero
from solo.llm import CommandMockLLM
from solo.inventory import InventoryEngine,new_item
from solo.maps import MapDescription
from solo.combat_service import CombatService,StartEncounter
from solo.geometry import rectangular_map,Position


class SmokeDice:
    def randint(self,a,b):return b


def make_app(folder,port):
    # Predetermined dice are exclusive to this disposable smoke process.
    import solo.combat,solo.rulesets
    solo.combat.secrets.SystemRandom=SmokeDice
    model=CommandMockLLM({'Осматриваю следы':dict(status='command',action=dict(kind='check',check_id='следы'),message='')})
    app=create_app(folder/'solo.sqlite3',model,local_port=port);store=app.state.store
    hero=Hero(name='Мирон',hp=16,max_hp=20,abilities=dict(strength=14,dexterity=18,constitution=14,intelligence=10,wisdom=12,charisma=10),conditions=['poisoned'],resources={'second_wind':dict(current=1,maximum=1)},weapon_proficiencies=['simple'])
    initial=dict(location='Каменный двор',turn=0,facts=[],deferred=[],locations=[dict(name='Каменный двор',description='Перед вами следы у ворот. Вдалеке ждёт страж.',npc=[],objects=['следы'],facts=[],checks={'следы':dict(ability='wisdom',skill='survival',dc=12)})])
    live=store.create('UI · вертикальный сценарий',hero,initial_state=initial)['id']
    def seed(state):
        from solo.world import ensure
        ensure(state)
        inv=InventoryEngine(state);bow=new_item('shortbow',1,'hero','Smoke fixture');inv.data.items.extend([bow,new_item('arrows',20,'hero','Smoke fixture')]);inv.equip(bow.id,'weapon');inv.flush()
        state['world']['pending_encounter']=dict(id='smoke-encounter',location=state['location'],description=MapDescription(environment='room').model_dump(),lethal_risk_announced=False,text='На дороге появился вооружённый страж. Начать столкновение?')
        return [dict(type='scripted',kind='fixture',text='Стартовая сцена подготовлена.')]
    store.record_scenario(live,'seed','Подготовка сцены',seed);store.save(live,'До проверки и боя')
    fixture=store.create('UI · общая фикстура',hero)['id']
    CombatService(store,rng=SmokeDice()).start(fixture,StartEncounter(request_id='fixture-battle',confirm=True,hero_weapon='shortbow',imported_map=rectangular_map(5,5),hero_position=Position(x=1,y=1),enemy_position=Position(x=2,y=1)))
    modes=dict(fixture=True,conflict=False)
    @app.middleware('http')
    async def smoke(request,call_next):
        if request.url.path==f'/api/campaigns/{fixture}/screen' and modes['fixture']:
            packet=json.loads((ROOT/'tests/fixtures/game_screen.json').read_text(encoding='utf-8'));packet['snapshot']['campaign_id']=fixture
            return JSONResponse(packet)
        if request.url.path.endswith('/screen/commands') and modes['conflict']:
            modes['conflict']=False
            return JSONResponse({'detail':{'code':'version_conflict','message':'Сцена изменилась. Обновите её и выберите действие снова.'}},status_code=409)
        return await call_next(request)
    @app.get('/__smoke__')
    def info():return dict(live=live,fixture=fixture,modes=modes,profile=str(folder),model_calls=model.metrics)
    @app.post('/__smoke__/{mode}')
    def control(mode:str):
        if mode=='real':modes['fixture']=False
        if mode=='conflict':modes['conflict']=True
        if mode in ('clarification','unsupported'):
            store.converse(live,mode,mode,'Что именно вы хотите осмотреть?' if mode=='clarification' else 'Эта механика не поддерживается; состояние не изменено.','clarification' if mode=='clarification' else 'notice')
        return modes
    return app


if __name__=='__main__':
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=19172);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='solo-play-screen-') as temporary:
        uvicorn.run(make_app(Path(temporary),args.port),host='127.0.0.1',port=args.port,log_level='warning',access_log=False)
