"""r220: свой инстанс приложения для браузерной приёмки игрового экрана.

Поднимает одно приложение на отдельном профиле (`runtime/r220-live/solo.sqlite3`)
и отдельном порту с офлайн-моком `PreviewMock`: платных вызовов нет, живой
профиль и порт 19215 не трогаются.

    SOLO_R220_DB   путь к sqlite профиля копии
    SOLO_R220_PORT порт на 127.0.0.1

Печатает `READY <pid>` после старта. Остановка — `kill <pid>`.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault('SOLO_TEST', '1')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from solo.app import create_app  # noqa: E402
from solo.preview_mock import PreviewMock  # noqa: E402

port = int(os.environ['SOLO_R220_PORT'])
database = os.environ['SOLO_R220_DB']
Path(database).parent.mkdir(parents=True, exist_ok=True)
app = create_app(database, PreviewMock(), local_port=port, experimental=True, offline_mock=True)
print('READY', os.getpid(), flush=True)
uvicorn.run(app, host='127.0.0.1', port=port, log_level='error', access_log=False)
