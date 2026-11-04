"""Isolated offline runs and source/data evidence, never a cloud/game profile."""
import hashlib, json, os, pathlib, socket, subprocess, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT/'reports/preview10'
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
OUT.mkdir(exist_ok=True)

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def snapshot(name):
    files = [p for base in ('solo','web','packaging','scripts','tests','prompts',
             'integration/nat20/imported','integration/nat20/playable','integration/nat20/releases','examples/lantern-post')
             for p in (ROOT/base).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [ROOT/p for p in ('desktop_main.py','README.md','assets/README-Windows.txt','vendor/nat20/UPSTREAM.json')]
    manifest = {p.relative_to(ROOT).as_posix():sha(p) for p in sorted(set(files))}
    digest = hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    (OUT/name).write_text(json.dumps(dict(source_sha256=digest,files=manifest),indent=2),encoding='utf-8')
    print('Source SHA256:', digest, flush=True)

mode = sys.argv[1]
if mode == 'snapshot':
    snapshot('build-source.json')
elif mode == 'preservation':
    baseline = json.loads((OUT/'protected-before.json').read_text())
    changed = [p for p,h in baseline.items() if not pathlib.Path(p).is_file() or sha(pathlib.Path(p)) != h]
    result = dict(checked_files=len(baseline),unchanged=not changed,changed=changed)
    (OUT/'preservation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    if changed: raise SystemExit(1)
elif mode in ('targeted','full'):
    if mode == 'full' and (OUT/'full.xml').exists():
        raise SystemExit('Full run already exists; do not overwrite or repeat it')
    snapshot(mode+'-source.json')
    os.environ.update(SOLO_TEST='1',PYTHONDONTWRITEBYTECODE='1')
    os.environ.pop('SOLO_NAT20_BLESS_STAND',None)
    connect,connect_ex = socket.socket.connect,socket.socket.connect_ex
    def allowed(address):
        if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
            raise RuntimeError('Acceptance forbids external/paid network')
    def local(sock,address):
        allowed(address)
        return connect(sock,address)
    def local_ex(sock,address):
        allowed(address)
        return connect_ex(sock,address)
    socket.socket.connect,socket.socket.connect_ex=local,local_ex
    import pytest
    targets = [] if mode == 'full' else [
        'tests/test_nat20_dash.py','tests/test_preview_dash_mapping.py','tests/test_dash_ui.py',
        'tests/test_small_window_ui.py','tests/test_preview9_clarification.py','tests/test_preview8_release.py',
        'tests/test_preview7_ui_followup.py','tests/test_preview7_acceptance.py','tests/test_nat20_api.py',
        'tests/test_nat20_command_cycle.py','tests/test_nat20_screen.py','tests/test_command_cycle.py',
        'tests/test_playable_ai_readiness.py','tests/test_playable_ui.py']
    raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','-o','faulthandler_timeout=90',
        '--basetemp='+str(OUT/('tmp-'+mode)),'--junitxml='+str(OUT/(mode+'.xml')),*targets]))
else:
    raise SystemExit('Expected snapshot, preservation, targeted or full')
