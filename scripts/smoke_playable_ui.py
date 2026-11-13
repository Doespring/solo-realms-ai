"""Disposable browser acceptance host. No cloud adapters or paid AI.

Run with --profile <owned temporary directory>; reuse it for a real server restart.
Only this test host exposes /__acceptance__. Production never registers it.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.environ['SOLO_TEST']='1'
from fastapi.responses import JSONResponse
from solo.app import create_app
from solo.contracts import Hero,Plan,Step
from solo.llm import CommandMockLLM,MockLLM


class BrowserMock(CommandMockLLM):
    async def stream(self,system,user,**options):
        try:packet=json.loads(user)
        except ValueError:packet={}
        stream=super().stream if options.get('mode')=='interpreter' or 'game_snapshot' in packet else MockLLM.stream.__get__(self)
        async for chunk in stream(system,user,**options):yield chunk


def make_app(profile,port):
    import solo.nat20_api
    # Trusted deterministic seed only in this mock process, never in the public form.
    solo.nat20_api.secrets.randbits=lambda bits:5
    actions={
        'Осматриваюсь':dict(kind='check',check_id='perception'),
        'Проверяю силу':dict(kind='check',check_id='strength'),
        'Начинаю бой':dict(kind='start_combat',encounter_id='basic'),
        'Подхожу к гоблину':dict(kind='move',position=dict(x=3,y=1,z=0)),
        'Атакую гоблина':dict(kind='attack',target='goblin'),
        'Атакую\nУточнение игрока: Гоблина':dict(kind='attack',target='goblin'),
        'Заканчиваю ход':dict(kind='end_turn'),
        'Завершаю бой':dict(kind='end_combat'),
        'Отдыхаю':dict(kind='short_rest',hit_dice=1),
        'Длительный отдых':dict(kind='long_rest',food_water=False)}
    intents={text:dict(status='command',action=action,message='') for text,action in actions.items()}
    intents.update({'Атакую':dict(status='clarification',action=None,message='Кого вы хотите атаковать?'),
                    'Лечу над двором':dict(status='unsupported',action=None,message='Полёт не поддерживается в этом испытании. Выберите другое действие.')})
    model=BrowserMock(intents)
    model.plan=Plan(summary='Осмотр',steps=[Step(kind='observe',text='Осмотр сцены')])
    app=create_app(profile/'acceptance.sqlite3',model,local_port=port)
    store=app.state.store
    if not store.list_campaigns():store.create('Обычная Solo · совместимость',Hero(name='Лада'))
    trace=json.loads((profile/'http-trace.json').read_text(encoding='utf-8')) if (profile/'http-trace.json').exists() else []
    mode={'conflict':False}
    @app.middleware('http')
    async def record(request,call_next):
        if mode['conflict'] and request.url.path.endswith('/commands'):
            mode['conflict']=False
            response=JSONResponse({'detail':dict(code='version_conflict',message='Сцена изменилась. Обновите её и выберите действие снова.')},status_code=409)
        else:response=await call_next(request)
        if request.method not in ('GET','HEAD'):
            trace.append(dict(method=request.method,path=request.url.path,status=response.status_code))
            (profile/'http-trace.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
        return response
    @app.get('/__acceptance__/info')
    def info():return dict(profile=str(profile),campaigns=store.list_campaigns(),model_calls=model.metrics,trace=trace)
    @app.post('/__acceptance__/conflict')
    def conflict():mode['conflict']=True;return mode
    @app.post('/__acceptance__/stop')
    def stop():app.state.smoke_server.should_exit=True;return dict(stopping=True)
    return app


if __name__=='__main__':
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--profile',required=True,type=Path);parser.add_argument('--port',default=19173,type=int);args=parser.parse_args()
    args.profile.mkdir(parents=True,exist_ok=True)
    app=make_app(args.profile,args.port)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,log_level='warning',access_log=False));app.state.smoke_server=server
    server.run()
