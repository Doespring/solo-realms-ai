"""r199 live run: существо САМО хватает героя, кнопка освобождения работает.

r198 показал обратное: в штатном пине монстр не мог наложить `grappled`, поэтому
кнопку «Вырваться из захвата» приходилось проверять на состоянии, которое
герой ставил себе сам (дыра `grapple target_id=hero`, закрыта в r199). Здесь
проверяется настоящий путь игрока по HTTP:

    старт боя → ход существа → на герое `grappled` с напечатанным КС
    → экран «Вырваться из захвата: <имя существа>» → нажатие → бросок
    (успех снимает условие, провал оставляет); оба исхода на одном сиде.

Приложение поднимается в фоновом потоке ЭТОГО процесса на 127.0.0.1:19223 со
своей базой `runtime/r199-probe` и `CommandMockLLM` (ни одного платного
вызова). Кампании — экспериментальные (своя встреча): в штатной `basic`
лежит один гоблин, а он по данным захватывать не умеет, и подменять штатный
реестр кампаний ради пробы нельзя. Всё остальное — ровно путь игрока:
`POST /api/campaigns/<id>/commands`, экран — `GET /api/campaigns/<id>/screen`.
Порт 19215 (живой стенд владельца) не трогается.
"""
import json
import re
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

PORT = 19223
BASE = 'http://127.0.0.1:%d' % PORT
DATA = ROOT / 'runtime' / 'r199-probe'

#: Существо → напечатанный в SRD 5.2.1 КС освобождения.
MONSTERS = {'bugbear-stalker': 13, 'constrictor-snake': 12}


def serve(path, port):
    import uvicorn
    from solo.app import create_app
    from solo.llm import CommandMockLLM
    app = create_app(path, CommandMockLLM({}), local_port=port, offline_mock=True)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    return server


def call(method, path, body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=120) as answer:
            return answer.status, json.loads(answer.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode('utf-8'))


def wait_ready(deadline=120):
    started = time.time()
    while time.time() - started < deadline:
        try:
            if call('GET', '/api/health')[0] == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def report(label, value):
    print(label + ':', json.dumps(value, ensure_ascii=False))


def make_campaign(name, monster, seed):
    """Своя встреча: одно существо в 5 футах от героя. Своя база, до сервера."""
    from nat20_builds import legal
    from solo.nat20_api import Nat20GameEngine
    from solo.storage import Store
    store = Store(DATA / 'r199-http.sqlite3')
    build = legal(class_slug='fighter', species_slug='human', level=3,
                  subclass_slug='champion',
                  equipment=['longsword', 'chain-mail', 'shield'])
    encounters = {'yard': {
        'grid': dict(width=7, height=5, cell_size_ft=5, cover_cells={}),
        'hero_position': dict(x=1, y=1, z=0),
        'actors': [dict(id='foe', monster=monster, position=dict(x=2, y=1, z=0))]}}
    created = Nat20GameEngine(store).create_experimental_campaign(
        name, build=build, encounters=encounters, seed=seed, title='Двор', description='д')
    runtime = store.campaign(created['id'])['state']['nat20']
    assert runtime['seed'] == seed, runtime['seed']
    return created['id']


def stored_conditions(cid):
    """Условия героя из СОСТОЯНИЯ кампании (тот же файл базы, не HTTP).

    Нужно, чтобы «на герое `grappled` с КС» было видно как запись, а не только
    как число в предложении журнала: публичный снимок HTTP отдаёт имена условий
    без КС (`solo/nat20_api.py`: `[{"id": s, **access}]`), а экран читает КС из
    `ActiveCondition`. Чтение — только для отчёта, все шаги игры идут по HTTP.
    """
    from solo.storage import Store
    state = Store(DATA / 'r199-http.sqlite3').campaign(cid)['state']['nat20']
    rows = (state.get('combat') or {}).get('view', {}).get('initiative') or []
    hero = next((row for row in rows if row.get('entity_id') == 'hero'), {})
    return [dict(condition=row.get('condition'), save_dc=row.get('save_dc'),
                 source_effect_id=row.get('source_effect_id'))
            for row in hero.get('conditions') or []]


def session(cid):
    def version():
        return call('GET', '/api/campaigns/' + cid)[1]['version']

    def command(request_id, **action):
        status, packet = call('POST', '/api/campaigns/%s/commands' % cid,
                              dict(request_id=request_id, expected_version=version(), action=action))
        if status != 200:
            return status, (packet.get('detail') or packet), packet
        events = packet.get('events') or []
        text = ((events[0].get('payload') or {}).get('outcome') if events else '') or ''
        return status, text, packet

    def screen():
        return call('GET', '/api/campaigns/%s/screen' % cid)[1]

    def view():
        return screen()['snapshot']['combat']

    def hero_row():
        return next(row for row in view()['actors'] if row['id'] == 'hero')

    def conditions():
        return [row if isinstance(row, str) else row.get('condition')
                for row in hero_row()['conditions']]

    def hero_turn(tag, limit=24):
        seen = []
        for attempt in range(limit):
            combat = view()
            if combat.get('ended'):
                return False, seen
            if combat.get('current_actor') == 'hero':
                return True, seen
            status, text, packet = command('%s-%d' % (tag, attempt), kind='end_turn')
            seen += [event.get('type') for event in (packet.get('events') or [])]
        return False, seen

    return dict(version=version, command=command, screen=screen, view=view,
                hero=hero_row, conditions=conditions, hero_turn=hero_turn)


def operations(screen):
    return [row.get('parameters', {}).get('operation') for row in screen.get('commands') or []
            if row.get('parameters', {}).get('kind') == 'native_action']


def escape_offer(screen):
    offers = [row for row in screen.get('commands') or []
              if (row.get('parameters') or {}).get('operation') == 'escape_grapple']
    return offers[0] if offers else None


#: Бросок освобождения в ответе HTTP приходит ТОЛЬКО предложением журнала:
#: `payload.effects` несёт эффекты и броски спасбросков/атак, а `CheckRolled`
#: нативной операции — нет (проверено: `native_events` в ответе маршрута не
#: отдаётся). Числа берём из того, что видит игрок, и сверяем с состоянием.
_ROLL_RE = re.compile(r'проверка (\d+) против КС (\d+)')


def press_escape(s, offer, tag):
    status, text, packet = s['command'](tag, **offer['parameters'])
    match = _ROLL_RE.search(text or '')
    roll, dc = (int(match.group(1)), int(match.group(2))) if match else (None, None)
    conditions = ['ended'] if s['view']().get('ended') else s['conditions']()
    report(tag + ': нажатие → ' + str(status), text)
    report(tag + ': состояние героя', conditions)
    return dict(status=status, text=text, roll=roll, dc=dc, conditions=conditions,
                succeeded='grappled' not in conditions)


def grapple_scenario(monster, seed):
    """Существо хватает героя СВОИМ ходом; герой освобождается (успех и провал)."""
    dc = MONSTERS[monster]
    print('\n== %s (сид %d): существо против героя' % (monster, seed))
    cid = make_campaign('r199-%s-%d' % (monster, seed), monster, seed)
    s = session(cid)
    status, text, _ = s['command']('start', kind='start_combat', encounter_id='yard')
    print('start_combat →', status, text)

    def held():
        return 'grappled' in s['conditions']()

    landed = None
    for attempt in range(10):
        ready, events = s['hero_turn']('monster-%d' % attempt)
        if not ready:
            break
        if held():
            landed = dict(hero=s['hero']()['conditions'])
            break
        s['command']('pass-%d' % attempt, kind='end_turn')
    if landed is None:
        report('ИТОГ: захват не встал', s['conditions']())
        return dict(monster=monster, landed=False)
    report('на герое после хода существа (снимок экрана)', s['hero']()['conditions'])
    report('на герое после хода существа (состояние кампании: условие + КС)',
           stored_conditions(cid))

    screen = s['screen']()
    offer = escape_offer(screen)
    report('экран: кнопки', [row.get('label') for row in screen.get('commands') or []
                            if (row.get('parameters') or {}).get('kind') == 'native_action'])
    report('экран: кнопка освобождения', offer)
    if offer is None:
        report('ИТОГ: кнопки нет', dict(operations=operations(screen)))
        return dict(monster=monster, landed=True, button=False)

    label = offer['label']
    outcomes = {}
    attempts = []
    for cycle in range(6):
        # До хода героя: пока ходит существо, экран действий не даёт. Ход
        # монстра может как схватить заново, так и добить героя.
        ready, _ = s['hero_turn']('round-%d' % cycle)
        if not ready:
            break
        if not held():
            s['command']('pass-%d' % cycle, kind='end_turn')
            continue
        offer = escape_offer(s['screen']())
        if offer is None:
            break
        result = press_escape(s, offer, 'escape-%d' % cycle)
        attempts.append(dict(roll=result['roll'], dc=result['dc'],
                             succeeded=result['succeeded'], text=result['text'],
                             conditions=result['conditions']))
        assert result['roll'] is not None and result['dc'] == dc, (result, dc)
        # SRD 5.2 «Ending a Grapple»: состояние снимается ровно тогда, когда
        # бросок дотянул до КС (движок сравнивает total >= dc).
        assert result['succeeded'] is (result['roll'] >= result['dc']), result
        outcomes[result['succeeded']] = outcomes.get(result['succeeded'], 0) + 1
        if len(outcomes) == 2:
            break
    report('исходы броска', outcomes)
    return dict(monster=monster, landed=True, dc=dc, button=True, label=label,
                outcomes={('success' if key else 'failure'): value for key, value in outcomes.items()},
                attempts=attempts)


def main():
    if DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True)
    server = serve(str(DATA / 'r199-http.sqlite3'), PORT)
    if not wait_ready():
        print('ОТКАЗ: приложение не поднялось на', PORT)
        return 1
    results = [grapple_scenario(monster, seed=1) for monster in MONSTERS]
    print('\nИТОГ:', json.dumps(results, ensure_ascii=False))
    server.should_exit = True
    ok = all(row.get('landed') and row.get('button')
             and set(row.get('outcomes') or {}) == {'success', 'failure'}
             for row in results)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
