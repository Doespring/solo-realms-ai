"""r173 live run: вдохновение и КЗ на настоящем HTTP, свой порт, офлайн-мок.

Доказывается путь игрока, а не класс: приложение поднимается на 127.0.0.1:19217
с ``CommandMockLLM`` (без сети и платных вызовов), и каждый ход — POST на
``/api/campaigns/<id>/combat/command``, тот же маршрут, что у экрана боя.

Ходы:
1. получение Героического вдохновения  -> в записи видно, что ресурс выдан;
2. атака с тратой вдохновения          -> в тексте формула «до переброса» и
   «вдохновение: переброшено X → Y»;
3. состояние кампании                  -> у героя 0 вдохновения (ресурс списан);
4. ответный ход противника             -> КЗ в его тексте равно КЗ в листе.

Порт 19215 (живой стенд владельца) не трогается; база лежит в
``runtime/r173-probe``.
"""
import json
import multiprocessing
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solo.contracts import Hero  # noqa: E402
from solo.geometry import Position, rectangular_map  # noqa: E402
from solo.storage import Store  # noqa: E402

PORT = 19217
BASE = 'http://127.0.0.1:%d' % PORT
STORE = ROOT / 'runtime' / 'r173-probe' / 'core-http.sqlite3'


def serve(path, port):
    import uvicorn
    from solo.app import create_app
    from solo.llm import CommandMockLLM
    app = create_app(path, CommandMockLLM({}), offline_mock=True)
    uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')).run()


def call(method, path, body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            return answer.status, json.loads(answer.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode('utf-8'))


def wait_ready(deadline=40):
    started = time.time()
    while time.time() - started < deadline:
        try:
            status, _ = call('GET', '/api/health')
            if status == 200:
                return
        except OSError:
            pass
        time.sleep(0.3)
    raise SystemExit('server did not start on %s' % BASE)


def main():
    if STORE.parent.exists():
        shutil.rmtree(STORE.parent)
    STORE.parent.mkdir(parents=True)
    store = Store(STORE)
    hero = Hero(name='Герой', species='human', weapon_proficiencies=['martial', 'simple'],
                abilities=dict(strength=14, dexterity=15, constitution=14,
                               intelligence=10, wisdom=12, charisma=10))
    campaign = store.create('r173 · живой прогон', hero)
    cid = campaign['id']

    server = multiprocessing.Process(target=serve, args=(str(STORE), PORT), daemon=True)
    server.start()
    try:
        wait_ready()

        status, started = call('POST', '/api/campaigns/%s/combat/start' % cid, dict(
            request_id='r173-start', confirm=True, hero_weapon='dagger',
            imported_map=rectangular_map(12, 10).model_dump(),
            hero_position=Position(x=1, y=1).model_dump(),
            enemy_position=Position(x=2, y=1).model_dump()))
        assert status == 200, (status, started)
        print('start:', started['results'][0]['text'])

        def turn(request_id, body):
            status, payload = call('POST', '/api/campaigns/%s/combat/command' % cid,
                                   dict(request_id=request_id, **body))
            assert status == 200, (request_id, status, payload)
            return payload['results'][0]['text']

        def hero_turn(tag):
            for attempt in range(8):
                _, view = call('GET', '/api/campaigns/%s/combat' % cid)
                if view.get('current_team') == 'hero':
                    return
                turn('%s-npc-%d' % (tag, attempt), dict(kind='npc_turn'))
            raise SystemExit('hero turn never arrived')

        hero_turn('r173-open')
        print('вдохновение:', turn('r173-gain', dict(kind='inspiration')))
        _, sheet = call('GET', '/api/campaigns/%s/character' % cid)
        sheet_ac = sheet['hero']['ac']
        _, before = call('GET', '/api/campaigns/%s' % cid)
        charges = before['state']['hero']['resources']['heroic_inspiration']['current']
        assert charges == 1, charges
        print('лист: КЗ =', sheet_ac, '| вдохновение в состоянии =', charges)

        shot = turn('r173-attack', dict(kind='attack', target='foe-1', inspiration=True))
        print('ход с тратой вдохновения:', shot)
        assert 'до переброса' in shot, shot
        assert 'вдохновение: переброшено' in shot, shot

        _, after = call('GET', '/api/campaigns/%s' % cid)
        left = after['state']['hero']['resources']['heroic_inspiration']['current']
        assert left == 0, left
        print('после хода: вдохновение в состоянии =', left)

        turn('r173-end', dict(kind='end_turn'))
        reply = turn('r173-npc', dict(kind='npc_turn'))
        print('ответный ход:', reply)
        found = re.findall(r'против КЗ (\d+)', reply)
        if found:
            assert int(found[0]) == sheet_ac, (found, sheet_ac)
            print('КЗ в бою = КЗ в листе =', sheet_ac)
        else:
            print('КЗ в ответном ходе не назван (противник не достал) — пропущено')
        print('OK: вдохновение и КЗ на порту %d, офлайн-мок, без внешних вызовов' % PORT)
    finally:
        server.terminate()
        server.join(timeout=10)


if __name__ == '__main__':
    main()
