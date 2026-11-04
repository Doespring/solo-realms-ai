"""Top-level native navigation policy, separate from HTTP asset/API requests."""
import ipaddress,json,re,threading
from urllib.parse import urlsplit,unquote
from fastapi.responses import FileResponse
from pydantic import Field
from .contracts import Strict

DOCUMENTS={
 'preparation-guide':dict(path='preparation-guide.md',title='Подготовка сценария · для разработчиков',advanced=True),
 'asset-attribution':dict(path='visual/ATTRIBUTION.md',title='Авторство и лицензии ресурсов',advanced=False),
 'player-help':dict(path='player-help.txt',title='Как вернуться к приключению',advanced=False),
}
DOCUMENT_PATHS={'/static/'+d['path']:id for id,d in DOCUMENTS.items()}
ROUTES={'play','character','skills','world','journal','adventure-handouts','campaigns','adventure-library','preparation','saves','master','performance','cloud','costs','rulesets','inventory','welcome','settings','master-library'}
ROUTES.update({'creation','character-creation'})

class NavigationPolicy:
    def __init__(self,port):self.origin=f'http://127.0.0.1:{port}';self.port=port
    def classify(self,url,*,bootstrap=False):
        if not isinstance(url,str) or len(url)>8192 or re.search(r'[\x00-\x20\\]',url):return ('blocked','')
        try:
            parsed=urlsplit(url);path=unquote(parsed.path)
            if parsed.username or parsed.password or re.search(r'[\x00-\x1f\\]',unquote(url)):return ('blocked','')
            if parsed.scheme=='http' and parsed.hostname=='127.0.0.1' and parsed.port==self.port:
                if path=='/' and not parsed.query and (not parsed.fragment or parsed.fragment in ROUTES):return ('app',parsed.fragment)
                if bootstrap and path=='/desktop/bootstrap':return ('bootstrap','')
                if not parsed.query and path in DOCUMENT_PATHS:return ('document',DOCUMENT_PATHS[path])
                if not parsed.query and path.startswith('/help/') and path[6:] in DOCUMENTS:return ('document',path[6:])
                if path=='/api/data/export' and not parsed.query:return ('download',url)
                return ('blocked','')
            if parsed.scheme=='blob' and url.startswith('blob:'+self.origin+'/'):return ('download',url)
            if parsed.scheme!='https' or not parsed.hostname or parsed.port not in (None,443):return ('blocked','')
            host=parsed.hostname.lower().rstrip('.')
            if host=='localhost' or host.endswith(('.localhost','.local','.internal','.test','.invalid')) or '.' not in host:return ('blocked','')
            try:
                if not ipaddress.ip_address(host).is_global:return ('blocked','')
            except ValueError:pass
            return ('external',url)
        except (ValueError,UnicodeError):return ('blocked','')

def register_document_api(app,root):
    class URLRequest(Strict):
        url: str=Field(min_length=1,max_length=8192)
        confirm: bool=False
    @app.post('/api/navigation/classify')
    def classify_url(body:URLRequest):
        kind,value=NavigationPolicy(app.state.local_port).classify(body.url)
        return dict(kind=kind,value=value)
    @app.post('/api/navigation/external')
    def external_browser(body:URLRequest):
        if not body.confirm or NavigationPolicy(app.state.local_port).classify(body.url)[0]!='external':raise ValueError('Внешний адрес не разрешён или открытие не подтверждено')
        import webbrowser
        if not webbrowser.open(body.url,new=2):raise ValueError('Не удалось открыть системный браузер. Игра остаётся доступной.')
        return dict(opened=True)
    @app.get('/api/help/{document}')
    def document_text(document:str):
        if document not in DOCUMENTS:raise KeyError('Документ не найден')
        definition=DOCUMENTS[document];path=root/'web'/definition['path']
        return dict(id=document,title=definition['title'],advanced=definition['advanced'],text=path.read_text(encoding='utf-8-sig'))
    def shell():return FileResponse(root/'web/index.html')
    # Defensive recovery: these documents never become a text-only dead end,
    # even in browsers or if the native top-level handler is unavailable.
    for id,definition in DOCUMENTS.items():
        app.add_api_route('/static/'+definition['path'],shell,methods=['GET'],include_in_schema=False)
        app.add_api_route('/help/'+id,shell,methods=['GET'],include_in_schema=False)

def install_native_navigation(window,port):
    policy=NavigationPolicy(port);control=window.native.webview;browser=window.native.browser
    ready=threading.Event();pending=[];lock=threading.Lock();bound=False
    def dispatch(kind,value):
        script='window.soloNavigation&&window.soloNavigation.receive('+json.dumps({'kind':kind,'value':value},ensure_ascii=False)+');'
        with lock:
            # Wrapper loaded callbacks run serially. A longer callback must not
            # delay document dispatch behind our own loaded callback.
            if window.events.loaded.is_set():ready.set()
            if not ready.is_set():pending.append(script);return
        # Never block a .NET UI callback waiting for ExecuteScriptAsync.
        threading.Thread(target=window.evaluate_js,args=(script,),daemon=True).start()
    def loaded():
        ready.set()
        with lock:queued=list(pending);pending.clear()
        for script in queued:threading.Thread(target=window.evaluate_js,args=(script,),daemon=True).start()
    window.events.loaded+=loaded
    def starting(sender,event):
        kind,value=policy.classify(str(event.Uri),bootstrap=not ready.is_set())
        if kind in ('app','bootstrap'):return
        if kind=='download' and bool(getattr(event,'IsUserInitiated',False)):return
        event.Cancel=True;dispatch(kind,value)
    control.NavigationStarting+=starting
    def attach(sender,event):
        nonlocal bound
        core=control.CoreWebView2
        if core is None or bound:return
        # Replace wrapper behaviour: it otherwise loads target=_blank in this window.
        core.NewWindowRequested-=browser.on_new_window_request
        def new_window(sender,event):
            event.Handled=True
            kind,value=policy.classify(str(event.Uri))
            if kind=='download':kind,value='blocked',''
            if kind=='external' and not bool(getattr(event,'IsUserInitiated',False)):kind,value='blocked',''
            dispatch(kind,value)
        core.NewWindowRequested+=new_window
        def source_changed(sender,event):
            kind,value=policy.classify(str(core.Source),bootstrap=not ready.is_set())
            if kind not in ('app','bootstrap','download'):
                ready.clear()
                with lock:pending.append('window.soloNavigation&&window.soloNavigation.receive('+json.dumps({'kind':kind,'value':value},ensure_ascii=False)+');')
                threading.Thread(target=window.load_url,args=(policy.origin+'/',),daemon=True).start()
        core.SourceChanged+=source_changed
        bound=True
        # Keep event delegates alive for the entire native window lifetime.
        window._solo_navigation=(policy,starting,attach,new_window,source_changed,loaded)
    control.CoreWebView2InitializationCompleted+=attach
    if control.CoreWebView2 is not None:attach(None,None)
    return policy
