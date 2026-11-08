"""Player notebook and free dice are auxiliary records, never canonical AI evidence."""
import json,secrets,time
from typing import Literal
from fastapi import Request,Response,HTTPException
from pydantic import Field
from .contracts import Strict

SCHEMA='''
CREATE TABLE IF NOT EXISTS player_notes(campaign TEXT PRIMARY KEY REFERENCES campaigns(id),text TEXT NOT NULL,revision INTEGER NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS player_note_requests(campaign TEXT NOT NULL REFERENCES campaigns(id),request_id TEXT NOT NULL,text TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(campaign,request_id));
CREATE TABLE IF NOT EXISTS player_dice(campaign TEXT NOT NULL REFERENCES campaigns(id),request_id TEXT NOT NULL,command TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(campaign,request_id));
'''
class Mode(Strict):confirm: Literal[True]
class Note(Strict):
    request_id: str=Field(min_length=1,max_length=80)
    text: str=Field(max_length=20000)
    expected_revision: int=Field(ge=0)
class Dice(Strict):
    request_id: str=Field(min_length=1,max_length=80)
    sides: Literal[4,6,8,10,12,20,100]=20
    count: int=Field(default=1,ge=1,le=20)
    modifier: int=Field(default=0,ge=-100,le=100)

def advanced_token_enabled(app,token):
    return app.state.advanced_sessions.get(token,0)>time.monotonic()
def advanced_enabled(request):
    return advanced_token_enabled(request.app,request.cookies.get('solo_advanced',''))
def require_advanced(request):
    if not advanced_enabled(request):raise HTTPException(403,'Ручное изменение механики доступно только в явно включённом Advanced / Developer Mode.')

def register_tabletop_api(app,store,game):
    app.state.advanced_sessions={}
    @app.get('/api/advanced/session')
    def mode(request:Request):return {'enabled':advanced_enabled(request)}
    @app.post('/api/advanced/session')
    def enable(body:Mode,response:Response):
        now=time.monotonic();app.state.advanced_sessions={k:v for k,v in app.state.advanced_sessions.items() if v>now}
        if len(app.state.advanced_sessions)>=32:raise ValueError('Слишком много активных сеансов редактора')
        token=secrets.token_urlsafe(32);app.state.advanced_sessions[token]=now+3600
        response.set_cookie('solo_advanced',token,httponly=True,samesite='strict',max_age=3600,path='/api')
        return {'enabled':True}
    @app.delete('/api/advanced/session')
    def disable(request:Request,response:Response):
        app.state.advanced_sessions.pop(request.cookies.get('solo_advanced',''),None);response.delete_cookie('solo_advanced',path='/api');return {'enabled':False}
    def notes(campaign,c):
        row=c.execute('SELECT text,revision,updated FROM player_notes WHERE campaign=?',(campaign,)).fetchone()
        return dict(row) if row else {'text':'','revision':0,'updated':None}
    @app.get('/api/campaigns/{campaign}/notes')
    def get_notes(campaign:str):
        with store.connection() as c:store.campaign(campaign,c);return notes(campaign,c)
    @app.put('/api/campaigns/{campaign}/notes')
    async def save_notes(campaign:str,body:Note):
        async with game.operation:
            with store.connection(True) as c:
                store.campaign(campaign,c);old=c.execute('SELECT text FROM player_note_requests WHERE campaign=? AND request_id=?',(campaign,body.request_id)).fetchone()
                if old:
                    if old['text']!=body.text:raise ValueError('Этот запрос относится к другой записи')
                    return notes(campaign,c)
                current=notes(campaign,c)
                if current['revision']!=body.expected_revision:raise ValueError('Блокнот изменился в другом окне. Обновите его перед сохранением.')
                revision=current['revision']+1
                c.execute('INSERT OR REPLACE INTO player_notes VALUES(?,?,?,?)',(campaign,body.text,revision,time.time()))
                c.execute('INSERT INTO player_note_requests VALUES(?,?,?,?)',(campaign,body.request_id,body.text,revision))
                return notes(campaign,c)
    @app.get('/api/campaigns/{campaign}/dice')
    def dice_history(campaign:str):
        with store.connection() as c:
            store.campaign(campaign,c)
            return [dict(request_id=r['request_id'],created=r['created'],**json.loads(r['result'])) for r in c.execute('SELECT * FROM player_dice WHERE campaign=? ORDER BY created DESC LIMIT 20',(campaign,))]
    @app.post('/api/campaigns/{campaign}/dice')
    async def roll(campaign:str,body:Dice):
        command=json.dumps(body.model_dump(exclude={'request_id'}),sort_keys=True)
        async with game.operation:
            with store.connection(True) as c:
                store.campaign(campaign,c);old=c.execute('SELECT * FROM player_dice WHERE campaign=? AND request_id=?',(campaign,body.request_id)).fetchone()
                if old:
                    if old['command']!=command:raise ValueError('Этот запрос относится к другому броску')
                    return json.loads(old['result'])
                values=[secrets.randbelow(body.sides)+1 for _ in range(body.count)]
                result=dict(dice=values,total=sum(values)+body.modifier,formula=f'{body.count}d{body.sides} {body.modifier:+d}',purpose='Свободный бросок. Не меняет мир и не заменяет проверку движка.')
                c.execute('INSERT INTO player_dice VALUES(?,?,?,?,?)',(campaign,body.request_id,command,json.dumps(result,ensure_ascii=False),time.time()));return result
