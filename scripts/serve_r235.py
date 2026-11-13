"""r235: инстанс приложения для браузерной приёмки каркаса.

Офлайн-мок `PreviewMock` (платных вызовов нет), отдельный профиль и порт:
живой профиль владельца и порт 19215 не трогаются.

    SOLO_R235_DB   путь к sqlite профиля копии
    SOLO_R235_PORT порт на 127.0.0.1

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

port = int(os.environ['SOLO_R235_PORT'])
database = os.environ['SOLO_R235_DB']
Path(database).parent.mkdir(parents=True, exist_ok=True)
app = create_app(database, PreviewMock(), local_port=port, experimental=True, offline_mock=True)
print('READY', os.getpid(), flush=True)
uvicorn.run(app, host='127.0.0.1', port=port, log_level='error', access_log=False)
