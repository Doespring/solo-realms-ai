"""Host profile selection only; no game rules and no implicit legacy imports."""
import json
import os
from pathlib import Path

PROFILE_NAME = 'SoloRealmsAI-Prototype-1'
MARKER = '.solo-prototype.json'


def default_profile(offline=False):
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local/share')))
    return base / (PROFILE_NAME+'-Offline' if offline else PROFILE_NAME)


def check_database(path):
    path = Path(path).resolve()
    protected = Path(__file__).resolve().parents[1] / 'data'
    if path.is_relative_to(protected.resolve()):
        raise ValueError('Проектная data/ защищена. Выберите отдельный профиль прототипа.')
    return path


def prepare_profile(path=None, *, offline=False):
    data = check_database(Path(path).expanduser() if path else default_profile(offline))
    marker = data / MARKER
    if marker.exists():
        if json.loads(marker.read_text(encoding='utf-8')).get('product') != PROFILE_NAME:
            raise ValueError('Каталог принадлежит другому профилю.')
    elif data.exists() and any(data.iterdir()):
        raise ValueError('Нужен новый пустой каталог. Старые профили автоматически не импортируются.')
    data.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        marker.write_text(json.dumps({'product': PROFILE_NAME, 'schema': 1}), encoding='utf-8')
    return data


def application_database(path=None):
    if path is None:
        explicit = os.environ.get('SOLO_DATA_DIR')
        if not explicit:
            raise ValueError('Не задан профиль. Запустите Desktop.cmd или укажите SOLO_DATA_DIR.')
        path = Path(explicit) / 'solo.sqlite3'
    return check_database(path)
