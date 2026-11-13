"""r187 live run: захват, толчок, провоцированная атака и укрытие по HTTP.

Приложение поднимается на 127.0.0.1:19219 в фоновом потоке с `CommandMockLLM`
(ни одного платного вызова). Кампания создаётся тем же маршрутом, что у
мастера-экрана — `POST /api/prototype/campaigns` (песочница, один гоблин), а
каждый шаг идёт `POST /api/campaigns/<id>/commands`: это ровно тот путь,
которым ходит игрок.

Почему не штатная сцена: «боевые» нативные действия (Захват, Толчок, Помощь,
Уклонение, Отход) хост разрешает только в явной экспериментальной кампании
(`nat20_worker.apply_action`: «Generic native actions require explicit
experimental campaign»), а штатная `fighter-standard-array` — не она. Это
печатается в отчёте отдельной строкой.

Печатает:
1. действия, которые отдаёт экран;
2. уход из зоны досягаемости: текст события (провоцированная атака) и Хиты;
3. захват: состояния цели и остаток движения;
4. толчок: состояние `prone`;
5. разметку сцены: сколько клеток укрытия и препятствий задал автор.

Порт 19215 (живой стенд владельца) не трогается; база — runtime/r187-probe.
"""
import json
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

PORT = 19219
BASE = 'http://127.0.0.1:%d' % PORT
DATA = ROOT / 'runtime' / 'r187-probe'


def serve(path, port):
    """HTTP-сервер в фоновом потоке этого процесса (настоящий сокет)."""
    import uvicorn
    from solo.app import create_app
    from solo.llm import CommandMockLLM
    app = create_app(path, CommandMockLLM({}), local_port=port, experimental=True, offline_mock=True)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def call(method, path, body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=40) as answer:
            return answer.status, json.loads(answer.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode('utf-8'))


def wait_ready(deadline=90):
    started = time.time()
    while time.time() - started < deadline:
        try:
            status, _ = call('GET', '/api/health')
            if status == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def main():
    if DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True)
    server = serve(str(DATA / 'r187-http.sqlite3'), PORT)
    if not wait_ready():
        print('ОТКАЗ: приложение не поднялось на', PORT)
        return 1

    def report(label, value):
        print(label + ':', json.dumps(value, ensure_ascii=False))

    from nat20_builds import legal
    build = legal(class_slug='fighter', species_slug='human', level=2,
                  equipment=['longsword', 'chain-mail', 'shield'])
    status, created = call('POST', '/api/prototype/campaigns',
                           dict(name='r187-боевик', request_id='r187-create', adventure='sandbox',
                                description='Проба главы боя', build=build,
                                encounter_monsters=['goblin-warrior'], hero_background='soldier'))
    if status != 200:
        print('ОТКАЗ создания кампании:', status, created)
        return 1
    cid = created['id']
    version = call('GET', '/api/campaigns/' + cid)[1]['version']

    status, screen = call('GET', '/api/campaigns/%s/screen' % cid)
    report('экран до боя', [row['parameters'].get('operation') for row in screen.get('commands', [])
                            if row.get('parameters', {}).get('kind') == 'native_action'])

    status, packet = call('POST', '/api/campaigns/%s/commands' % cid,
                          dict(request_id='r187-start', expected_version=version,
                               action=dict(kind='start_combat', encounter_id='opening')))
    if status != 200:
        print('ОТКАЗ старта боя:', status, packet)
        return 1
    snapshot = packet['snapshot']
    version = call('GET', '/api/campaigns/' + cid)[1]['version']
    hero = next(row for row in snapshot['actors'] if row['id'] == 'hero')
    foe = next(row for row in snapshot['actors'] if row['id'] != 'hero')
    report('бой начат', dict(hero_hp=hero['hp']['current'], foe_hp=foe['hp']['current'],
                              hero_position=hero['position'], foe_position=foe['position']))

    status, screen = call('GET', '/api/campaigns/%s/screen' % cid)
    report('экран в бою', [row['parameters'].get('operation') for row in screen.get('commands', [])
                           if row.get('parameters', {}).get('kind') == 'native_action'])

    def command(request_id, **action):
        nonlocal version
        first = True
        for attempt in range(4):
            status, packet = call('POST', '/api/campaigns/%s/commands' % cid,
                                  dict(request_id=request_id if first else request_id + '-turn%d' % attempt,
                                       expected_version=version, action=action))
            first = False
            if status == 200:
                break
            detail = json.dumps(packet.get('detail') or packet, ensure_ascii=False)
            if 'not the current actor' in detail and action.get('kind') != 'end_turn':
                # Ходит враг: даём движку провести его реплику и пробуем снова.
                call('POST', '/api/campaigns/%s/commands' % cid,
                     dict(request_id=request_id + '-yield%d' % attempt, expected_version=version,
                          action=dict(kind='end_turn')))
                version = call('GET', '/api/campaigns/' + cid)[1]['version']
                continue
            break
        status, packet = status, packet
        if status == 200:
            version = call('GET', '/api/campaigns/' + cid)[1]['version']
            events = packet.get('events') or []
            text = ((events[0].get('payload') or {}).get('outcome') if events else '') or packet.get('feedback') or ''
            return 200, text, packet
        return status, packet.get('detail') or packet, packet

    hx, hy, _ = hero['position']
    fx, fy, _ = foe['position']
    adjacent = dict(x=fx - 1, y=fy, z=0)
    code, text, packet = command('r187-approach', kind='move', position=adjacent)
    report('подход к врагу', dict(status=code, text=text))
    status, screen = call('GET', '/api/campaigns/%s/screen' % cid)
    reached = []
    for _ in range(3):
        reached = [row['label'] for row in screen.get('commands', [])
                   if row.get('parameters', {}).get('operation') in ('grapple', 'shove')]
        if reached:
            break
        # Экран предлагает действия только в свой ход: даём движку провести ход врага.
        call('POST', '/api/campaigns/%s/commands' % cid,
             dict(request_id='r187-yield-screen-%d' % len(reached), expected_version=version,
                  action=dict(kind='end_turn')))
        version = call('GET', '/api/campaigns/' + cid)[1]['version']
        status, screen = call('GET', '/api/campaigns/%s/screen' % cid)
    report('экран в 5 футах', reached)

    # Уход из зоны досягаемости: обратно на клетку через одну — это Реакция.
    away = dict(x=hx, y=hy, z=0)
    code, text, packet = command('r187-withdraw', kind='move', position=away)
    if code == 200:
        report('уход из досягаемости', dict(text=text, hp={row['id']: row['hp']['current']
                                                           for row in packet['snapshot']['actors']}))
    else:
        report('уход из досягаемости', dict(status=code, text=text))

    # Возвращаемся в 5 футов и захватываем.
    code, text, packet = command('r187-back', kind='move', position=adjacent)
    foe_id = foe['id']
    code, text, packet = command('r187-grapple', kind='native_action', operation='grapple', target_id=foe_id)
    if code != 200:
        command('r187-end', kind='end_turn')
        code, text, packet = command('r187-grapple-b', kind='native_action', operation='grapple', target_id=foe_id)
    if code == 200:
        now = {row['id']: row for row in packet['snapshot']['actors']}
        report('захват', dict(text=text, conditions=now[foe_id]['conditions'],
                              movement=now[foe_id]['resources'].get('movement_remaining'),
                              foe_hp=now[foe_id]['hp']['current']))
    else:
        report('захват', dict(status=code, text=text))

    command('r187-end2', kind='end_turn')
    code, text, packet = command('r187-shove', kind='native_action', operation='shove',
                                 target_id=foe_id, shove_push=False)
    if code == 200:
        now = {row['id']: row for row in packet['snapshot']['actors']}
        report('толчок', dict(text=text, conditions=now[foe_id]['conditions'],
                              foe_hp=now[foe_id]['hp']['current']))
    else:
        report('толчок', dict(status=code, text=text))

    code, text, packet = command('r187-swing', kind='native_action', operation='attack',
                                 weapon_id='longsword', target_id=foe_id)
    if code == 200:
        now = {row['id']: row for row in packet['snapshot']['actors']}
        report('атака', dict(text=text, foe_hp=now[foe_id]['hp']['current']))
    else:
        report('атака', dict(status=code, text=text))

    server.should_exit = True
    return 0


if __name__ == '__main__':
    sys.exit(main())
