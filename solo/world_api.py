"""World API and allowlisted text commands; never returns the raw scenario script."""
import asyncio,json,re
from .contracts import Strict
from pydantic import Field
from .world import project
from .world_service import (WORLD_MAX_BYTES,WORLD_SUFFIX,WorldCommand,WorldImport,WorldPackageProblem,WorldService,
    export_package,import_world_package,inspect_package,library,library_package,parse_package,safe_file,save_package,
    world_folder)

class TravelPreview(Strict):
    target: str=Field(min_length=1,max_length=120)
class WorldSave(Strict):
    name: str=Field(default='',max_length=100)
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
async def world_text(request):
    """Тело запроса как текст пакета: предел и UTF-8 — одни для импорта и осмотра.

    Предел проверяется до чтения, чтобы чужой файл в сотни мегабайт не занимал
    память сервера.
    """
    declared=int(request.headers.get('content-length') or 0)
    if declared>WORLD_MAX_BYTES:raise WorldPackageProblem('Файл мира больше '+str(WORLD_MAX_BYTES//1048576)+' МБ — это не пакет мира.')
    raw=await request.body()
    if len(raw)>WORLD_MAX_BYTES:raise WorldPackageProblem('Файл мира больше '+str(WORLD_MAX_BYTES//1048576)+' МБ — это не пакет мира.')
    try:return raw.decode('utf-8')
    except UnicodeDecodeError:raise WorldPackageProblem('Файл мира не читается как текст UTF-8. Выберите файл, сохранённый приложением (*'+WORLD_SUFFIX+').') from None

def register_world_api(app,store,game):
    from fastapi import Request,Response
    from fastapi.responses import FileResponse
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
    # --- r236: готовые миры ---------------------------------------------------
    # Мир, разобранный один раз, уходит файлом и поднимается без вызовов модели.
    @app.get('/api/campaigns/{campaign}/world/export')
    def world_export(campaign:str):
        package=export_package(store,campaign)
        body=json.dumps(package,ensure_ascii=False,indent=1)
        return Response(content=body,media_type='application/json',headers={
            'Content-Disposition':'attachment; filename="'+package['short_name']+WORLD_SUFFIX+'"',
            'Cache-Control':'private, no-store'})
    @app.post('/api/campaigns/{campaign}/world/save')
    async def world_save(campaign:str,body:WorldSave|None=None):
        async with game.operation:
            saved=await asyncio.to_thread(save_package,store,campaign,name=(body.name if body else ''))
            saved['folder']='runtime/'+world_folder(store).name
            return saved
    @app.get('/api/worlds')
    def worlds():
        folder=world_folder(store)
        return dict(worlds=library(store),folder=str(folder),storage='runtime/'+folder.name)
    @app.post('/api/worlds/inspect')
    async def worlds_inspect(request:Request):
        # Осмотр до импорта: имя кампании, система и совместимость. Пакет
        # определяется СОДЕРЖИМЫМ (format/format_version и схема), а не именем файла:
        # браузер дописывает «(1)» к скачанному файлу, и по имени такой файл
        # отвергался, хотя содержимое то же самое (аудит 08.11, F06).
        try:raw=parse_package(await world_text(request))
        except WorldPackageProblem as exc:
            return dict(ok=False,compatible=False,compatibility='несовместим с этой сборкой',
                reason=str(exc),name='',system='',format='',format_version=None)
        return inspect_package(raw)
    @app.post('/api/worlds/import')
    async def worlds_import(request:Request):
        # Файл приходит телом запроса: экран читает выбранный файл и отправляет его
        # текст как есть.
        package=parse_package(await world_text(request))
        async with game.operation:return await asyncio.to_thread(import_world_package,store,package)
    @app.post('/api/worlds/{name}/play')
    async def worlds_play(name:str):
        package=library_package(store,name)
        async with game.operation:return await asyncio.to_thread(import_world_package,store,package)
    @app.get('/api/worlds/{name}/download')
    def worlds_download(name:str):
        path=safe_file(store,name)
        return FileResponse(path,media_type='application/json',filename=name,headers={'Cache-Control':'private, no-store'})
