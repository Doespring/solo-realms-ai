"""One full acceptance run with an external-network guard and isolated temp root."""
import hashlib,json,os,pathlib,socket,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path.insert(0,str(ROOT));os.environ.update(SOLO_TEST='1',PYTHONDONTWRITEBYTECODE='1')
connect=socket.socket.connect
def local_only(sock,address):
    if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
        raise RuntimeError('Acceptance forbids external/paid network')
    return connect(sock,address)
socket.socket.connect=local_only
files=[p for base in ('solo','web','packaging','scripts','tests') for p in (ROOT/base).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
(ROOT/'reports/preview7/full-test-source.json').write_text(json.dumps({p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files},indent=2),encoding='utf-8')
import pytest
raise SystemExit(pytest.main(['-v','-p','no:cacheprovider','-o','faulthandler_timeout=90',
    '--basetemp=reports/preview7/tmp-full','--junitxml=reports/preview7/full.xml']))
