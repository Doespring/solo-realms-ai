"""Small native window controller; only bounded preferences are exposed to the local UI."""
import json,secrets,threading
from pathlib import Path
from pydantic import Field
from fastapi.responses import JSONResponse,RedirectResponse
from .contracts import Strict
from .version import VERSION
from .user_data import backup

class WindowPreferences(Strict):
    width: int=Field(default=1280,ge=960,le=2560)
    height: int=Field(default=800,ge=640,le=1440)
    fullscreen: bool=False
    port: int=Field(default=19161,ge=1024,le=65535)

class DesktopHost:
    def __init__(self,data):
        self.exit_requested=False
        self.data=Path(data);self.path=self.data/'desktop.json';self.lock=threading.RLock();self.window=None;self.session=secrets.token_urlsafe(32);self.bootstrap=secrets.token_urlsafe(32)
        self.preferences=WindowPreferences.model_validate(json.loads(self.path.read_text(encoding='utf-8'))) if self.path.exists() else WindowPreferences()
    def persist(self):
        with self.lock:
            temporary=self.path.with_suffix('.tmp');temporary.write_text(self.preferences.model_dump_json(),encoding='utf-8');temporary.replace(self.path)
    def status(self):return dict(version=VERSION,data_dir=str(self.data),preferences=self.preferences.model_dump(),window_ready=self.window is not None,exit_requested=self.exit_requested,updates='Обновление выполняется новым установщиком с сохранением профиля. Автозагрузки обновлений нет.')
    def apply(self,body):
        with self.lock:
            if not self.window:raise ValueError('Окно ещё не готово')
            if body.port!=self.preferences.port:raise ValueError('Порт текущего окна нельзя менять во время работы')
            previous=self.preferences.model_copy(deep=True);self.preferences=body
            try:
                if previous.fullscreen!=body.fullscreen:self.window.toggle_fullscreen()
                if not body.fullscreen:self.window.resize(body.width,body.height)
                self.persist()
            except BaseException:
                self.preferences=previous;self.persist();raise
            return self.status()

def register_desktop(app,host,game):
    app.state.desktop=host
    @app.middleware('http')
    async def session_only(request,call_next):
        if request.url.path=='/desktop/bootstrap':
            token=request.query_params.get('token','')
            with host.lock:
                if not host.bootstrap or not secrets.compare_digest(token,host.bootstrap):return JSONResponse({'detail':'Окно не авторизовано'},status_code=403)
                host.bootstrap=None
            response=RedirectResponse('/',status_code=303);response.set_cookie('solo_window',host.session,httponly=True,samesite='strict',path='/')
            response.headers['Cache-Control']='no-store';response.headers['Referrer-Policy']='no-referrer';return response
        if not secrets.compare_digest(request.cookies.get('solo_window',''),host.session):return JSONResponse({'detail':'Доступ разрешён только окну приложения'},status_code=403)
        response=await call_next(request);response.headers['Cache-Control']='no-store';return response
    @app.post('/api/desktop/preferences')
    def preferences(body:WindowPreferences):return host.apply(body)
    @app.post('/api/desktop/backup')
    async def save_backup():
        async with game.operation:return dict(path=str(backup(host.data)))
    class RestoreRequest(Strict):
        backup: str=Field(min_length=1,max_length=140)
        confirm: bool=False
    @app.post('/api/desktop/restore')
    async def restore(body:RestoreRequest):
        if not body.confirm:raise ValueError('Подтвердите восстановление профиля')
        from .data_management import plan_restore
        async with game.operation:result=plan_restore(host.data,body.backup)
        threading.Timer(.4,host.window.destroy).start();return result
    @app.post('/api/desktop/exit')
    async def exit_app():
        if not host.window:raise ValueError('Окно ещё не готово')
        host.exit_requested=True
        # Closing disconnects SSE; the existing host waits for graceful shutdown and saves a copy.
        threading.Timer(.15,host.window.destroy).start();return {'closing':True}
