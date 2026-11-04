"""Windows entry point. Embedded backend and WebView2; no system Python or separate browser."""
import os,sys,socket,time,threading,json,logging,argparse
from pathlib import Path
from solo.user_data import profile_dir,prepare,ProfileLock,InstallMutex,import_legacy,backup
from solo.desktop_host import DesktopHost

def run(args):
    experimental = args.experimental or (Path(__file__).resolve().parent/'prototype.json').exists()
    preview = not experimental and ((Path(__file__).resolve().parent/'preview-mock.json').exists() or args.mock_preview)
    offline = experimental and (args.offline_mock or args.mock_preview)
    if args.offline_mock and not experimental:raise ValueError('--offline-mock requires --experimental')
    pdf_phase=getattr(args,'smoke_pdf_phase',None)
    if pdf_phase:
        from solo.desktop_observations import validate_pdf_smoke_profile
        if not experimental:raise ValueError('PDF fixture requires experimental mode')
        validate_pdf_smoke_profile(args.data_dir,pdf_phase)
    if experimental:
        os.environ.pop('SOLO_NAT20_BLESS_STAND',None)
        if args.import_data_dir:raise ValueError('Experimental mode does not import old profiles or keys')
        from solo.prototype_profile import prepare_profile
        data=prepare_profile(args.data_dir,offline=offline)
    else:
        data=Path(args.data_dir).resolve() if args.data_dir else (profile_dir().with_name('SoloRealmsAI-Preview10-Mock') if preview else profile_dir());data.mkdir(parents=True,exist_ok=True)
    with ProfileLock(data),InstallMutex():
        from solo.data_management import consume_restore
        consume_restore(data)
        if args.smoke_test and (data/'solo.sqlite3').exists():
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(data/'solo.sqlite3')) as db:
                if any(not name.startswith('EXE smoke ·') for name, in db.execute('SELECT name FROM campaigns')):raise ValueError('Smoke test refuses a profile containing player campaigns')
        if args.import_data_dir:import_legacy(args.import_data_dir,data)
        mark_ready=prepare(data)
        os.environ['SOLO_TEST']='1' # The executable constructs its one app explicitly, with a chosen profile.
        from solo.app import create_app,ROOT
        import uvicorn,webview
        from solo.desktop_host import register_desktop
        host=DesktopHost(data);sock=socket.socket();sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:sock.bind(('127.0.0.1',host.preferences.port))
        except OSError:sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1];sock.listen(128);host.preferences.port=port;host.persist()
        if pdf_phase:
            from scripts.smoke_fixture import make_pdf_world_smoke_llm
            model=make_pdf_world_smoke_llm(data,smoke_test=True,phase=pdf_phase)
        elif preview or offline:
            from solo.preview_mock import PreviewMock
            model=PreviewMock()
            # Explicit reproducible mock preview, never a production RNG default.
            if preview:
                import solo.nat20_api
                solo.nat20_api.secrets.randbits=lambda bits:5
        elif args.smoke_test:
            from scripts.smoke_fixture import DesktopFixture
            model=DesktopFixture()
        else:model=None
        app=create_app(data/'solo.sqlite3',model,local_port=port,experimental=experimental,offline_mock=preview or offline);register_desktop(app,host,app.state.game);mark_ready()
        if preview:
            @app.middleware('http')
            async def mock_label(request,call_next):
                response=await call_next(request)
                if request.url.path=='/':
                    from starlette.responses import HTMLResponse
                    html=(ROOT/'web/index.html').read_text(encoding='utf-8').replace('<body>', '<body><div role="status" style="padding:8px;text-align:center;background:#483c21;color:#fff">WINDOWS PREVIEW · MOCK AI · без облачных запросов · ограниченная сцена D&amp;D 2024</div>')
                    return HTMLResponse(html)
                return response
        logging.basicConfig(filename=data/'desktop.log',level=logging.INFO,encoding='utf-8',format='%(asctime)s %(levelname)s %(message)s')
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_config=None,timeout_graceful_shutdown=4))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start();deadline=time.monotonic()+30
        while not server.started:
            if not thread.is_alive() or time.monotonic()>deadline:raise RuntimeError('Не удалось запустить встроенный backend')
            time.sleep(.05)
        webview.settings['ALLOW_DOWNLOADS']=True;webview.settings['ALLOW_FILE_URLS']=False;webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
        title='Solo Realms AI — EXPERIMENTAL' if experimental else 'Solo Realms AI — MOCK PREVIEW' if preview else 'Solo Realms AI — Obsidian Chronicle'
        window=webview.create_window(title,f'http://127.0.0.1:{port}/desktop/bootstrap?token={host.bootstrap}',width=host.preferences.width,height=host.preferences.height,min_size=(960,640),fullscreen=host.preferences.fullscreen,hidden=args.smoke_test,background_color='#171a1e',text_select=True)
        host.window=window
        # Renderer callbacks keep the installed window on its own loopback origin.
        def native_ready():
            from solo.navigation import install_native_navigation
            install_native_navigation(window,port)
        window.events.before_show+=native_ready
        def on_resize(width,height):
            if host.preferences.fullscreen:return
            if 960<=width<=2560 and 640<=height<=1440:host.preferences.width=width;host.preferences.height=height;host.persist()
        window.events.resized+=on_resize
        smoke={};start=time.perf_counter();smoke_started=threading.Event()
        def test_loaded():
            if not args.smoke_test or smoke_started.is_set():return
            smoke_started.set()
            try:
                import httpx,psutil
                # Exercise the real embedded server, not the Python TestClient.
                with httpx.Client(base_url=f'http://127.0.0.1:{port}',cookies={'solo_window':host.session},trust_env=False,timeout=30 if pdf_phase else 15) as client:
                    result=client.get('/api/health');result.raise_for_status();index=client.get('/');index.raise_for_status()
                    assert 'world.js' in index.text
                    if getattr(args,'smoke_scope','legacy')=='pdf-world':
                        from solo.desktop_observations import pdf_world_smoke,pdf_prepared_smoke
                        probe_started=time.perf_counter()
                        probe=pdf_prepared_smoke(client,window,app,data,pdf_phase) if pdf_phase else pdf_world_smoke(client,window,app)
                        smoke.update(ok=True,frozen=bool(getattr(sys,'frozen',False)),python=sys.version.split()[0],
                            native_window_created=True,renderer='edgechromium',mock_only=True,paid_calls=0,
                            pdf_world=probe,ready_ms=round((probe_started-start)*1000,1),
                            probe_ms=round((time.perf_counter()-probe_started)*1000,1))
                        return
                    created=client.post('/api/campaigns',json={'name':'EXE smoke · тестовые данные','hero':{'name':'Тестовый герой'}});created.raise_for_status();cid=created.json()['id']
                    turn=client.post(f'/api/campaigns/{cid}/play',json={'request_id':'walk','text':'Иду к старой башне.'});turn.raise_for_status()
                    canonical=client.get(f'/api/campaigns/{cid}').json()['state'];assert canonical['elapsed_minutes']==30
                    host.apply(host.preferences.model_copy(update={'width':1024,'height':720}));host.apply(host.preferences.model_copy(update={'fullscreen':True}))
                    assert window.native.is_fullscreen
                    host.apply(host.preferences.model_copy(update={'fullscreen':False}))
                    assert window.width==1024 and window.height==720 and not window.native.is_fullscreen
                    unauthorized=httpx.get(f'http://127.0.0.1:{port}/api/campaigns',trust_env=False).status_code;assert unauthorized==403
                    smoke.update(ok=True,frozen=bool(getattr(sys,'frozen',False)),python=sys.version.split()[0],native_window_created=True,renderer='edgechromium',window_controls=True,backend_health=result.json()['cloud_only'],journey_minutes=30,unauthorized_status=unauthorized,ready_ms=round((time.perf_counter()-start)*1000,1),rss_mib=round(psutil.Process().memory_info().rss/1048576,1),mock_only=True)
                    from solo.desktop_observations import obsidian_smoke
                    process=psutil.Process();smoke['process_tree_rss_mib']=round(sum(p.memory_info().rss for p in [process,*process.children(recursive=True)] if p.is_running())/1048576,1)
                    smoke['obsidian']=obsidian_smoke(client,window,app)
            except BaseException as exc:
                smoke.update(ok=False,error=type(exc).__name__+': '+str(exc));logging.exception('Native smoke failed')
            finally:
                if not host.exit_requested:window.destroy()
        window.events.loaded+=test_loaded
        timer=threading.Timer(90 if pdf_phase else 45,window.destroy) if args.smoke_test else None
        if timer:timer.start()
        try:webview.start(gui='edgechromium',private_mode=False,storage_path=str(data/'webview'),icon=str(ROOT/'assets/solo.ico'))
        finally:
            if timer:timer.cancel()
            server.should_exit=True;thread.join(10)
            if thread.is_alive():server.force_exit=True;thread.join(3)
            sock.close()
            if not thread.is_alive():backup(data,'after-close')
            if args.smoke_test:
                smoke['backend_stopped']=not thread.is_alive();Path(args.smoke_test).write_text(json.dumps(smoke,ensure_ascii=False,indent=2),encoding='utf-8')
        if args.smoke_test and not smoke.get('ok'):raise RuntimeError('Smoke test failed; see its JSON report')

def main():
    if '--nat20-worker' in sys.argv:
        from solo.nat20_worker import main as worker
        # Windowed PyInstaller has no Python stdio objects. The parent provides
        # explicit inherited pipes; reconstruct only those worker handles.
        if sys.stdin is None or sys.stdout is None:
            import ctypes,msvcrt
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GetStdHandle.restype=ctypes.c_void_p
            sys.stdin=os.fdopen(msvcrt.open_osfhandle(kernel.GetStdHandle(-10),os.O_RDONLY),'r',encoding='utf-8')
            sys.stdout=os.fdopen(msvcrt.open_osfhandle(kernel.GetStdHandle(-11),os.O_WRONLY),'w',encoding='utf-8')
        # Redirected windowed streams can otherwise inherit Windows ANSI;
        # the parent/worker JSON protocol is always UTF-8, including Cyrillic.
        sys.stdin.reconfigure(encoding='utf-8')
        sys.stdout.reconfigure(encoding='utf-8')
        worker();return
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir');parser.add_argument('--import-data-dir');parser.add_argument('--mock-preview',action='store_true');parser.add_argument('--experimental',action='store_true');parser.add_argument('--offline-mock',action='store_true');parser.add_argument('--smoke-test',metavar='REPORT_JSON');parser.add_argument('--smoke-scope',choices=('legacy','pdf-world'),default='legacy');parser.add_argument('--adventure-worker',nargs=2);parser.add_argument('--adventure-render',nargs=3)
    parser.add_argument('--smoke-pdf-phase',choices=('prepare','continue'))
    args=parser.parse_args()
    if args.adventure_worker:
        from solo.adventure_worker import run as run_import
        run_import(*args.adventure_worker);return
    if args.adventure_render:
        from solo.adventure_worker import render_material
        from solo.adventure_library import AdventureLibrary
        data,id,width=args.adventure_render;render_material(AdventureLibrary(data),id,int(width));return
    if args.smoke_test and not args.data_dir:parser.error('Smoke test requires an isolated --data-dir')
    if args.smoke_scope=='pdf-world' and (not args.smoke_test or not args.offline_mock):parser.error('PDF world smoke requires explicit isolated offline mock smoke')
    if args.smoke_pdf_phase and not (args.smoke_test and args.smoke_scope=='pdf-world' and args.offline_mock and args.data_dir):
        parser.error('PDF fixture requires explicit isolated offline pdf-world smoke')
    try:run(args)
    except BaseException as exc:
        if args.smoke_test:
            report=Path(args.smoke_test)
            # Preserve the original assertion diagnostic from the loaded callback.
            if not report.exists() or json.loads(report.read_text(encoding='utf-8')).get('ok'):
                report.write_text(json.dumps(dict(ok=False,error=type(exc).__name__+': '+str(exc)),ensure_ascii=False,indent=2),encoding='utf-8')
        elif os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,'Не удалось открыть Solo Realms AI.\n'+str(exc),'Solo Realms AI',0x10)
        raise SystemExit(1)
if __name__=='__main__':main()
