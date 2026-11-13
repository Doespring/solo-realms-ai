"""Дочерний сервер r189: одно приложение на одном профиле, гасится файлом-токеном.

Запускается только из `probe_r189_long_session.py`. Нужен отдельным процессом,
потому что смысл проверки — пережить настоящую смерть процесса, а не новый
объект приложения в том же интерпретаторе.

Переменные окружения:
    SOLO_CHILD_DB    путь к sqlite-файлу профиля (общий для обеих фаз);
    SOLO_CHILD_PORT  порт на 127.0.0.1;
    SOLO_CHILD_STOP  путь к токену: как только файл появился, сервер закрывается.
"""
import os

os.environ['SOLO_TEST'] = '1'
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402


def main() -> int:
    from solo.app import create_app
    from solo.preview_mock import PreviewMock

    port = int(os.environ['SOLO_CHILD_PORT'])
    stop = os.environ['SOLO_CHILD_STOP']
    app = create_app(os.environ['SOLO_CHILD_DB'], PreviewMock(), local_port=port,
                     experimental=True, offline_mock=True)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error',
                                           access_log=False, timeout_graceful_shutdown=5))

    def serve():
        try:
            server.run()
        except BaseException as exc:  # noqa: BLE001 - печатаем причину смерти
            print('RUNERR', type(exc).__name__, str(exc)[:160], flush=True)

    threading.Thread(target=serve, daemon=True).start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(.05)
    print('READY', server.started, os.getpid(), flush=True)
    if not server.started:
        return 2
    try:
        sys.stdin.close()
    except Exception:
        pass
    while not os.path.exists(stop):
        time.sleep(.05)
    server.should_exit = True
    time.sleep(.1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
