"""r238: приложение отдельным процессом, чтобы его можно было перезапустить.

Живая проверка «разбор переживает перезапуск» требует именно процесса: если
держать приложение в тесте, перезапускать нечего. Профиль берётся готовый
(`--data-dir`), офлайн-мок не включается: прогон должен идти в настоящую модель.

    .venv/bin/python scripts/serve_r238.py --data-dir reports/r238/live --port 19216

Печатает `READY <pid>` после старта; остановка — SIGTERM (штатный shutdown).
"""
import argparse,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--port',type=int,default=19216)
    parser.add_argument('--log',default='')
    args=parser.parse_args()

    sys.path.insert(0,str(ROOT))
    import uvicorn
    from solo.app import create_app

    data=Path(args.data_dir).resolve()
    files=[path for path in ('solo.sqlite3','adventure.sqlite3','preparation.sqlite3') if (data/path).is_file()]
    if 'solo.sqlite3' not in files:raise SystemExit('Нет профиля в '+str(data))
    app=create_app(data/'solo.sqlite3',None,local_port=args.port,experimental=True)
    print('READY',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',access_log=False)
    return 0


if __name__=='__main__':raise SystemExit(main())
