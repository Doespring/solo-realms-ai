"""r222: что игровой экран отдаёт про бой и карту.

Печатает ключи snapshot.combat до и после start_combat, позиции участников,
метки команд «Перейти» и наличие геометрии (grid/cells/objects). Это проба
посылки карточки: рисовать карту можно только из того, что реально пришло.
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault('SOLO_TEST', '1')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from fastapi.testclient import TestClient  # noqa: E402

from solo.app import create_app  # noqa: E402
from solo.llm import CommandMockLLM  # noqa: E402
from solo.nat20_api import Nat20GameEngine  # noqa: E402
from solo.storage import Store  # noqa: E402
from test_nat20_command_cycle import ACTIONS  # noqa: E402


def model():
    return CommandMockLLM({key: dict(status='command', action=value, message='')
                           for key, value in ACTIONS.items()})


def main():
    base = Path(os.environ.get('SOLO_R222_DB', str(ROOT / 'runtime' / 'r222-probe' / 'probe.sqlite3')))
    base.parent.mkdir(parents=True, exist_ok=True)
    for leftover in base.parent.glob('probe.sqlite3*'):
        leftover.unlink()
    store = Store(base)
    with TestClient(create_app(store.path, model())) as client:
        if os.environ.get('SOLO_R222_SCENE'):
            created = client.post('/api/campaigns', json=dict(name='Проба карты', scene=os.environ['SOLO_R222_SCENE']))
            print('создание:', created.status_code, str(created.text)[:200])
            cid = created.json()['id']
        else:
            cid = Nat20GameEngine(store).create_campaign('Проба карты', seed=5)['id']
        print('кампания', cid)
        screen = client.get(f'/api/campaigns/{cid}/screen').json()
        print('combat до боя:', json.dumps(screen['snapshot'].get('combat'), ensure_ascii=False)[:200])
        print('keys snapshot:', sorted(screen['snapshot']))
        print('все команды:', [c['label'] for c in screen['commands']])
        starts = [c for c in screen['commands'] if c['parameters'].get('kind') == 'start_combat']
        print('start_combat команд:', [(c['label'], c['parameters']) for c in starts])
        if not starts:
            print('НЕТ команды start_combat')
            return 1
        command = starts[0]
        answer = client.post(f'/api/campaigns/{cid}/screen/commands', json=dict(
            request_id='r222-start', expected_version=command['expected_version'],
            kind=command['kind'], parameters=command['parameters']))
        print('старт боя:', answer.status_code, str(answer.json())[:200])
        screen = client.get(f'/api/campaigns/{cid}/screen').json()
        battle = screen['snapshot'].get('combat') or {}
        print('combat keys:', sorted(battle))
        print('combat:', json.dumps(battle, ensure_ascii=False)[:1500])
        print('команды боя:', [c['label'] for c in screen['commands'] if c['parameters'].get('kind') in ('move', 'attack', 'end_turn')][:12])
        print('прототип /prototype targets:')
        proto = client.get(f'/api/campaigns/{cid}/prototype')
        if proto.status_code == 200:
            print('  ', json.dumps(proto.json().get('targets'), ensure_ascii=False)[:400])
        else:
            print('  ', proto.status_code, str(proto.text)[:200])
        camp = client.get(f'/api/campaigns/{cid}').json()
        print('кампания combat:', json.dumps(camp['state'].get('combat'), ensure_ascii=False)[:300])
        print('кампания keys state:', sorted(camp['state']))
        combat_endpoint = client.get(f'/api/campaigns/{cid}/combat')
        print('/combat:', combat_endpoint.status_code, str(combat_endpoint.text)[:200])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
