"""Narrow offline clarification regression; deliberately no automatic full run."""
import hashlib,json,os,pathlib,socket,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/preview9'
os.chdir(ROOT);sys.path.insert(0,str(ROOT));OUT.mkdir(exist_ok=True)

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

mode=sys.argv[1]
if mode=='preservation':
    baseline=json.loads((OUT/'protected-before.json').read_text())
    changed=[p for p,h in baseline.items() if not pathlib.Path(p).is_file() or sha(pathlib.Path(p))!=h]
    result=dict(checked_files=len(baseline),unchanged=not changed,changed=changed)
    (OUT/'preservation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    if changed:raise SystemExit(1)
elif mode in ('snapshot','targeted'):
    files=[p for base in ('solo','web','packaging','scripts','tests','prompts','integration/nat20/imported',
           'integration/nat20/playable','integration/nat20/releases','examples/lantern-post')
           for p in (ROOT/base).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    files += [ROOT/p for p in ('desktop_main.py','README.md','assets/README-Windows.txt','vendor/nat20/UPSTREAM.json')]
    manifest={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(set(files))}
    digest=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    name='build-source.json' if mode=='snapshot' else 'targeted-source.json'
    (OUT/name).write_text(json.dumps(dict(source_sha256=digest,files=manifest),indent=2),encoding='utf-8')
    print('Source SHA256:',digest,flush=True)
    if mode=='snapshot':raise SystemExit(0)
    os.environ.update(SOLO_TEST='1',PYTHONDONTWRITEBYTECODE='1')
    os.environ.pop('SOLO_NAT20_BLESS_STAND',None)
    original=socket.socket.connect
    def local(sock,address):
        if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):raise RuntimeError('External/paid network forbidden')
        return original(sock,address)
    socket.socket.connect=local
    import pytest
    raise SystemExit(pytest.main(['-q','-p','no:cacheprovider','--basetemp='+str(OUT/'tmp-targeted'),
        '--junitxml='+str(OUT/'targeted.xml'),'tests/test_preview9_clarification.py',
        'tests/test_nat20_command_cycle.py','tests/test_command_cycle.py','tests/test_playable_ai_readiness.py',
        'tests/test_preview7_acceptance.py','tests/test_preview8_release.py','tests/test_preview7_ui_followup.py']))
else:raise SystemExit('Only targeted, snapshot and preservation are authorized')
