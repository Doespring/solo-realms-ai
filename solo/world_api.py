"""World API and allowlisted text commands; never returns the raw scenario script."""
import re
from .contracts import Strict
from pydantic import Field
from .world import project
from .world_service import WorldCommand,WorldImport,WorldService

class TravelPreview(Strict):
    target: str=Field(min_length=1,max_length=120)
def text_command(text,request_id):
    value=text.lower().strip().rstrip('.! ')
    if value=='отклоняю предложение столкновения':return WorldCommand(request_id=request_id,kind='decline_encounter')
    exploration=re.fullmatch(r'исследую\s+(?:неизвестную\s+)?дорогу\s+на\s+(север|юг|восток|запад)',value)
    if exploration:return WorldCommand(request_id=request_id,kind='explore',direction=exploration[1])
    route=re.fullmatch(r'исследую дорогу (?:номер )?(\d{1,3})',value)
    if route:return WorldCommand(request_id=request_id,kind='explore',exit=str(int(route[1])-1))
    marker=re.fullmatch(r'ставлю метку:\s*(.+?);\s*координаты:\s*(\d{1,2})\s*,\s*(\d{1,2})',text,re.I)
    if marker:return WorldCommand(request_id=request_id,kind='marker',text=marker[1],coordinates=[int(marker[2]),int(marker[3])])
    delete=re.fullmatch(r'удаляю метку\s+([a-z0-9-]{36})',value)
    if delete:return WorldCommand(request_id=request_id,kind='delete_marker',marker=delete[1])
    mode=re.fullmatch(r'подтверждаю режим:\s*(сюжетный|песочница|смешанный);\s*выбор пути на карте:\s*(да|нет)',value)
    if mode:return WorldCommand(request_id=request_id,kind='settings',confirm=True,settings=dict(mode={'сюжетный':'story','песочница':'sandbox','смешанный':'mixed'}[mode[1]],map_travel=mode[2]=='да'))
    return None
def register_world_api(app,store,game):
    service=WorldService(store)
    @app.get('/api/campaigns/{campaign}/world')
    def world(campaign:str):return project(store.campaign(campaign)['state'])
    @app.post('/api/campaigns/{campaign}/world/preview')
    def preview(campaign:str,body:TravelPreview):return service.preview(campaign,body.target)
    @app.post('/api/campaigns/{campaign}/world/command')
    async def command(campaign:str,body:WorldCommand):
        async with game.operation:return game.public_event(service.execute(campaign,body,from_map=body.kind in ('travel','explore')))
    @app.post('/api/campaigns/{campaign}/world/import')
    async def import_world(campaign:str,body:WorldImport):
        async with game.operation:return game.public_event(service.import_world(campaign,body))
