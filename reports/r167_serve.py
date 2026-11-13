"""r167: the same offline-mock server, started without the Windows-only profile lock.

scripts/start_cloud.py wraps the application in solo.user_data.ProfileLock, which
imports msvcrt and therefore exists only on Windows. On this Linux box the card's
"own port, own profile" run needs the same application without that lock; nothing
else differs: the profile is still prepared by prepare_profile, the model is
still PreviewMock, and the port is passed to the app.

    SOLO_TEST=1 .venv/bin/python reports/r167_serve.py --port 19318 --data-dir runtime/r167-live
"""
import argparse
import os
import sys
from pathlib import Path

os.environ['SOLO_TEST'] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default='runtime/r167-live')
    parser.add_argument('--port', type=int, default=19318)
    args = parser.parse_args()
    from solo.prototype_profile import prepare_profile
    from solo.app import create_app
    from solo.preview_mock import PreviewMock
    import uvicorn
    data = prepare_profile(ROOT / args.data_dir, offline=True)
    app = create_app(data / 'solo.sqlite3', PreviewMock(), local_port=args.port,
                     experimental=True, offline_mock=True)
    print('r167 stand: http://127.0.0.1:%d/ profile=%s' % (args.port, data), flush=True)
    uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()
