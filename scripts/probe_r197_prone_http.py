"""r197 live run: «Лечь» по HTTP — журнал, экран и состояние героя.

Приложение поднимается на 127.0.0.1:19219 в фоновом потоке этого процесса с
`CommandMockLLM` (ни одного платного вызова). Кампания создаётся тем же
маршрутом, что у мастера-экрана — `POST /api/prototype/campaigns` (песочница,
один гоблин), а шаг идёт `POST /api/campaigns/<id>/commands`: ровно тот путь,
которым ходит игрок.

Печатает:
1. экран до боя: есть ли кнопка «Лечь» (`operation=drop_prone`);
2. нажатие «Лечь»: строка журнала (outcome из события движка);
3. состояние героя в снимке боя (`prone`) и в экране (список состояний);
4. экран после: «Лечь» исчезла, «Встать» на месте;
5. повторное «Лечь»: причина отказа по-человечески (`already_prone`);
6. «Встать»: состояние ушло, движение потрачено на половину скорости.

Порт 19215 (живой стенд владельца) не трогается; база — runtime/r197-probe.
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
DATA = ROOT / 'runtime' / 'r197-probe'


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


def conds(rows):
    return [row if isinstance(row, str) else row.get('condition') for row in rows or []]


def operations(screen):
    return [row.get('parameters', {}).get('operation') for row in screen.get('commands') or []
            if row.get('parameters', {}).get('kind') == 'native_action']


def labels(screen):
    return [row.get('label') for row in screen.get('commands') or []
            if row.get('parameters', {}).get('kind') == 'native_action']


def main():
    if DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True)
    server = serve(str(DATA / 'r197-http.sqlite3'), PORT)
    if not wait_ready():
        print('ОТКАЗ: приложение не поднялось на', PORT)
        return 1

    def report(label, value):
        print(label + ':', json.dumps(value, ensure_ascii=False))

    from nat20_builds import legal
    build = legal(class_slug='fighter', species_slug='human', level=2,
                  equipment=['longsword', 'chain-mail', 'shield'])
    status, created = call('POST', '/api/prototype/campaigns',
                           dict(name='r197-лечь', request_id='r197-create', adventure='sandbox',
                                description='Проба добровольного «лечь»', build=build,
                                encounter_monsters=['goblin-warrior'], hero_background='soldier'))
    if status != 200:
        print('ОТКАЗ создания кампании:', status, created)
        return 1
    cid = created['id']

    def version():
        return call('GET', '/api/campaigns/' + cid)[1]['version']

    def command(request_id, **action):
        for attempt in range(4):
            status, packet = call('POST', '/api/campaigns/%s/commands' % cid,
                                  dict(request_id=request_id + ('' if attempt == 0 else '-t%d' % attempt),
                                       expected_version=version(), action=action))
            if status == 200:
                break
            detail = json.dumps(packet.get('detail') or packet, ensure_ascii=False)
            if 'not the current actor' in detail and action.get('kind') != 'end_turn':
                call('POST', '/api/campaigns/%s/commands' % cid,
                     dict(request_id=request_id + '-yield%d' % attempt, expected_version=version(),
                          action=dict(kind='end_turn')))
                continue
            break
        if status == 200:
            events = packet.get('events') or []
            text = ((events[0].get('payload') or {}).get('outcome') if events else '') or packet.get('feedback') or ''
            return 200, text, packet
        return status, packet.get('detail') or packet, packet

    status, screen = call('GET', '/api/campaigns/%s/screen' % cid)
    report('экран до боя: кнопки', labels(screen))

    status, text, packet = command('r197-start', kind='start_combat', encounter_id='opening')
    if status != 200:
        print('ОТКАЗ старта боя:', status, packet)
        return 1
    def hero_turn_screen(attempts=4):
        """Экран своего хода: пока ходит враг, список команд пуст."""
        got = None
        for attempt in range(attempts):
            status, got = call('GET', '/api/campaigns/%s/screen' % cid)
            # Штатный экран отдаёт кнопки нативных действий только внутри боя:
            # проекция до `start_combat` живёт без них (замер: 6 команд вместо 10).
            if got.get('commands') and ((got.get('snapshot') or {}).get('combat') or {}).get('active'):
                return got
            call('POST', '/api/campaigns/%s/commands' % cid,
                 dict(request_id='r197-yield%d' % attempt, expected_version=version(),
                      action=dict(kind='end_turn')))
        return got

    screen = hero_turn_screen()
    report('экран в бою: кнопки', labels(screen))
    report('«Лечь» предложена', 'drop_prone' in operations(screen))

    hero_before = next(row for row in packet['snapshot']['actors'] if row['id'] == 'hero')
    status, text, packet = command('r197-prone', kind='native_action', operation='drop_prone')
    if status != 200:
        print('ОТКАЗ «Лечь»:', status, text)
        return 1
    hero_after = next(row for row in packet['snapshot']['actors'] if row['id'] == 'hero')
    report('журнал «Лечь»', text)
    report('состояние героя в снимке',
           dict(before=conds(hero_before['conditions']),
                after=conds(hero_after['conditions']),
                movement_before=hero_before['resources'].get('movement_remaining'),
                movement_after=hero_after['resources'].get('movement_remaining')))

    status, screen = call('GET', '/api/campaigns/%s/screen' % cid)
    hero_screen = (screen.get('snapshot') or {}).get('hero') or {}
    report('экран после «Лечь»', dict(hero_conditions=conds(hero_screen.get('conditions')),
                                     drop_prone='drop_prone' in operations(screen),
                                     stand_up='stand_up' in operations(screen)))

    status, text, packet = command('r197-prone-again', kind='native_action', operation='drop_prone')
    report('повторное «Лечь»', dict(status=status, answer=text if status != 200 else '(принято)'))

    status, text, packet = command('r197-stand', kind='native_action', operation='stand_up')
    if status == 200:
        hero_now = next(row for row in packet['snapshot']['actors'] if row['id'] == 'hero')
        report('журнал «Встать»', dict(text=text, conditions=conds(hero_now['conditions']),
                                       movement=hero_now['resources'].get('movement_remaining')))
    else:
        report('ОТКАЗ «Встать»', dict(status=status, answer=text))

    server.should_exit = True
    return 0


if __name__ == '__main__':
    sys.exit(main())
