"""Isolated browser acceptance host; same explicit mock as the Windows preview."""
import argparse, os, socket, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.environ['SOLO_TEST']='1'
from solo.app import create_app
from solo.preview_mock import PreviewMock
import solo.nat20_api
solo.nat20_api.secrets.randbits=lambda bits:5
original=socket.socket.connect
def local_only(sock,address):
    if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):raise RuntimeError('Offline preview forbids external network')
    return original(sock,address)
socket.socket.connect=local_only
if __name__=='__main__':
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument('--profile',required=True,type=Path);parser.add_argument('--port',type=int,default=19179);args=parser.parse_args()
    args.profile.mkdir(parents=True,exist_ok=True)
    uvicorn.run(create_app(args.profile/'solo.sqlite3',PreviewMock(),local_port=args.port),host='127.0.0.1',port=args.port,log_level='warning')
