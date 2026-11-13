"""Explicit isolated browser host for the same experimental backend/frontend."""
import os
os.environ['SOLO_TEST']='1'
os.environ.pop('SOLO_NAT20_BLESS_STAND',None)
import argparse,sys,threading,webbrowser
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir');parser.add_argument('--port',type=int,default=19215)
    parser.add_argument('--offline-mock',action='store_true');parser.add_argument('--open',action='store_true')
    args=parser.parse_args()
    from solo.prototype_profile import prepare_profile
    from solo.user_data import ProfileLock
    data=prepare_profile(args.data_dir,offline=args.offline_mock)
    with ProfileLock(data):
        from solo.app import create_app
        from solo.preview_mock import PreviewMock
        import uvicorn
        model=PreviewMock() if args.offline_mock else None
        app=create_app(data/'solo.sqlite3',model,local_port=args.port,experimental=True,offline_mock=args.offline_mock)
        url=f'http://127.0.0.1:{args.port}/'
        print(f'Experimental Solo Realms AI: {url}; profile: {data}',flush=True)
        if args.open:threading.Timer(1,lambda:webbrowser.open(url)).start()
        uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)

if __name__=='__main__':main()
