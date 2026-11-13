"""n00: живой сервер на копии профиля без единого платного вызова.

Экран подготовки проверяется браузером на настоящем профиле r239 (книга уже
разобрана), но мастер — офлайн-мок: разговорный ход не тратит денег владельца.
Сторож подготовки при `offline_mock` не поднимается, поэтому спящие прогоны
копии не продолжаются.

    .venv/bin/python scripts/serve_n00.py --data-dir runtime/n00-live-2023 --port 19277
"""
import argparse,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--port',type=int,default=19277)
    args=parser.parse_args()

    sys.path.insert(0,str(ROOT))
    import uvicorn
    from solo.app import create_app
    from solo.llm import MockLLM

    data=Path(args.data_dir).resolve()
    if not (data/'solo.sqlite3').is_file():raise SystemExit('Нет профиля в '+str(data))
    app=create_app(data/'solo.sqlite3',MockLLM(),local_port=args.port,experimental=True,offline_mock=True)
    print('READY',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning',access_log=False)
    return 0


if __name__=='__main__':raise SystemExit(main())
