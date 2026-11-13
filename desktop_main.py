"""Windows entry point. Embedded backend and WebView2; no system Python or separate browser."""
import os,sys,socket,time,threading,json,logging,argparse
from pathlib import Path
from solo.user_data import profile_dir,prepare,ProfileLock,InstallMutex,import_legacy,backup
from solo.desktop_host import DesktopHost

def run(args):
    data=Path(args.data_dir).resolve() if args.data_dir else profile_dir();data.mkdir(parents=True,exist_ok=True)
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
        from solo.smoke_fixture import DesktopFixture
        import uvicorn,webview
        from solo.desktop_host import register_desktop
        host=DesktopHost(data);sock=socket.socket();sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        try:sock.bind(('127.0.0.1',host.preferences.port))
        except OSError:sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1];sock.listen(128);host.preferences.port=port;host.persist()
        app=create_app(data/'solo.sqlite3',DesktopFixture() if args.smoke_test else None,local_port=port);register_desktop(app,host,app.state.game);mark_ready()
        logging.basicConfig(filename=data/'desktop.log',level=logging.INFO,encoding='utf-8',format='%(asctime)s %(levelname)s %(message)s')
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_config=None,timeout_graceful_shutdown=4))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start();deadline=time.monotonic()+30
        while not server.started:
            if not thread.is_alive() or time.monotonic()>deadline:raise RuntimeError('Не удалось запустить встроенный backend')
            time.sleep(.05)
        webview.settings['ALLOW_DOWNLOADS']=True;webview.settings['ALLOW_FILE_URLS']=False;webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER']=False
        window=webview.create_window('Solo Realms AI — Obsidian Chronicle',f'http://127.0.0.1:{port}/desktop/bootstrap?token={host.bootstrap}',width=host.preferences.width,height=host.preferences.height,min_size=(960,640),fullscreen=host.preferences.fullscreen,hidden=args.smoke_test,background_color='#171a1e',text_select=True)
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
                with httpx.Client(base_url=f'http://127.0.0.1:{port}',cookies={'solo_window':host.session},trust_env=False,timeout=15) as client:
                    result=client.get('/api/health');result.raise_for_status();index=client.get('/');index.raise_for_status()
                    assert 'world.js' in index.text
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
        timer=threading.Timer(45,window.destroy) if args.smoke_test else None
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
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir');parser.add_argument('--import-data-dir');parser.add_argument('--smoke-test',metavar='REPORT_JSON');parser.add_argument('--adventure-worker',nargs=2);parser.add_argument('--adventure-render',nargs=3);args=parser.parse_args()
    if args.adventure_worker:
        from solo.adventure_worker import run as run_import
        run_import(*args.adventure_worker);return
    if args.adventure_render:
        from solo.adventure_worker import render_material
        from solo.adventure_library import AdventureLibrary
        data,id,width=args.adventure_render;render_material(AdventureLibrary(data),id,int(width));return
    if args.smoke_test and not args.data_dir:parser.error('Smoke test requires an isolated --data-dir')
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
