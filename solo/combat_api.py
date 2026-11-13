"""Public combat controller; text and map buttons use CombatService."""
import re
from .combat_service import CombatService,CombatCommand,StartEncounter
from .combat_view import project
from .combat import CombatEngine
from .geometry import Position
from .contracts import Strict
from .maps import TEMPLATES,MapDescription,generate

class MovePreview(Strict):
    position: Position

def text_command(text,state,request_id):
    normalized=text.lower().strip().rstrip('.! ')
    exact={'рывок':'dash','уклоняюсь':'dodge','уклонение':'dodge','отход':'disengage','встаю':'stand_up','встать':'stand_up','заканчиваю ход':'end_turn','конец хода':'end_turn','ход противника':'npc_turn','ход npc':'npc_turn','реакция: атакую':'reaction','реакция: отказываюсь':'reaction','завершить бой':'finish','осматриваюсь':'observe'}
    if normalized in exact:
        return CombatCommand(request_id=request_id,kind=exact[normalized],choice=('attack' if normalized.endswith('атакую') else 'decline') if exact[normalized]=='reaction' else None)
    move=re.fullmatch(r'(?:иду|перехожу|двигаюсь)\s+(?:на\s+)?клетку\s+(\d{1,2})\s*[,; ]\s*(\d{1,2})(?:\s*[,; ]\s*(-?\d{1,2}))?',normalized)
    if move:return CombatCommand(request_id=request_id,kind='move',position=dict(x=int(move[1])-1,y=int(move[2])-1,z=int(move[3] or 0)))
    attack=re.fullmatch(r'(?:атакую|бью)\s+(.+)',normalized)
    if attack:
        targets=[a for a in project(state)['actors'] if a['team']=='enemy' and (attack[1] in (a['id'],a['name'].lower()) or (len(attack[1].split())==1 and len(a['name'].split())==1 and attack[1][:5]==a['name'].lower()[:5]))]
        if len(targets)==1:return CombatCommand(request_id=request_id,kind='attack',target=targets[0]['id'])
    fall=re.fullmatch(r'(подтверждаю\s+)?падение\s+на\s+клетку\s+(\d{1,2})\s*[,; ]\s*(\d{1,2})\s*[,; ]\s*(-?\d{1,2})',normalized)
    if fall:return CombatCommand(request_id=request_id,kind='drop',position=dict(x=int(fall[2])-1,y=int(fall[3])-1,z=int(fall[4])),confirm=bool(fall[1]))
    search=re.fullmatch(r'ищу\s+на\s+клетке\s+(\d{1,2})\s*[,; ]\s*(\d{1,2})(?:\s*[,; ]\s*(-?\d{1,2}))?',normalized)
    if search:return CombatCommand(request_id=request_id,kind='search',position=dict(x=int(search[1])-1,y=int(search[2])-1,z=int(search[3] or 0)))
    interact=re.fullmatch(r'(?:открываю|закрываю|переключаю)\s+(.+)',normalized)
    if interact:
        objects=[o for o in project(state)['map']['objects'] if interact[1] in (o['id'].lower(),o['name'].lower())]
        if len(objects)==1:return CombatCommand(request_id=request_id,kind='interact',target=objects[0]['id'])
    return None

def plan_command(plan,state,request_id):
    if plan.unclear or plan.branches:raise ValueError('Уточните одно действие в этом ходу')
    if len(plan.steps)!=1:raise ValueError('Начните с одного действия; следующие шаги зависят от его результата')
    step=plan.steps[0]
    if step.disposition!='now' or step.condition:raise ValueError('Это намерение сейчас не исполняется. Когда будете готовы, укажите действие отдельно')
    if step.kind=='observe':return CombatCommand(request_id=request_id,kind='observe')
    if step.kind=='speak':return CombatCommand(request_id=request_id,kind='speak',text=step.text,target=step.target)
    if step.kind=='attack':
        targets=[a for a in project(state)['actors'] if a['team']=='enemy' and step.target.lower() in (a['name'].lower(),a['id'].lower())]
        if len(targets)==1:return CombatCommand(request_id=request_id,kind='attack',target=targets[0]['id'])
        raise ValueError('Укажите одну видимую цель')
    if step.kind=='move':
        command=text_command(step.text,state,request_id)
        if command and command.kind=='move':return command
        raise ValueError('Укажите клетку: «Иду на клетку 4, 5» или выберите её на карте')
    raise ValueError('Эта боевая команда пока не зарегистрирована. Доступны движение, атака, рывок, отход, уклонение и завершение хода')

def register_combat_api(app,store,game):
    service=CombatService(store)
    @app.get('/api/maps/templates')
    def templates():return [dict(id=id,name=name) for id,name in TEMPLATES.items()]
    @app.get('/api/campaigns/{campaign}/combat')
    def combat(campaign:str):return project(store.campaign(campaign)['state'])
    @app.post('/api/campaigns/{campaign}/combat/move-preview')
    def preview(campaign:str,body:MovePreview):
        engine=CombatEngine(store.campaign(campaign)['state'])
        path,cost=engine.movement_path('hero',body.position.key())
        return dict(path=[dict(x=p[0],y=p[1],z=p[2]) for p in path],cost_ft=cost)
    @app.post('/api/campaigns/{campaign}/combat/fall-preview')
    def fall_preview(campaign:str,body:MovePreview):
        return CombatEngine(store.campaign(campaign)['state']).fall_preview('hero',body.position.key())
    @app.post('/api/campaigns/{campaign}/combat/start')
    async def start(campaign:str,body:StartEncounter):
        async with game.operation:return game.public_event(service.start(campaign,body))
    @app.post('/api/campaigns/{campaign}/combat/command')
    async def command(campaign:str,body:CombatCommand):
        async with game.operation:return game.public_event(service.execute(campaign,body))
