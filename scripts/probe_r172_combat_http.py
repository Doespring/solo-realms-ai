"""r172 live run: three combat turns over real HTTP on its own port, offline mock.

Proves the player path, not the class: the app is served on 127.0.0.1:19216 with
``CommandMockLLM`` (no network, no paid call), and every turn is a POST to
``/api/campaigns/<id>/combat/command`` — the same route the battle screen uses.

Turns:
1. shortbow shot next to a hostile  -> roll text must carry "помеха: рядом враг";
2. attack on an invisible target    -> "помеха: ... цель не видна";
3. move into the creature's cell    -> 409 with the rule's refusal, not silence.

Port 19215 (the owner's live stand) is never touched; the store lives under
``runtime/r172-probe``.
"""
import json
import multiprocessing
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from solo.combat_service import CombatService, StartEncounter  # noqa: E402
from solo.contracts import Hero  # noqa: E402
from solo.geometry import Position, rectangular_map  # noqa: E402
from solo.storage import Store  # noqa: E402

PORT = 19216
BASE = 'http://127.0.0.1:%d' % PORT
STORE = ROOT / 'runtime' / 'r172-probe' / 'combat-http.sqlite3'


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
    campaign = store.create('r172 · живой прогон',
                            Hero(name='Герой', weapon_proficiencies=['martial']))
    cid = campaign['id']

    server = multiprocessing.Process(target=serve, args=(str(STORE), PORT), daemon=True)
    server.start()
    try:
        wait_ready()

        status, started = call('POST', '/api/campaigns/%s/combat/start' % cid, dict(
            request_id='r172-start', confirm=True, hero_weapon='shortbow',
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
            # Initiative is rolled, so the first actor is not guaranteed to be the hero.
            for attempt in range(6):
                _, view = call('GET', '/api/campaigns/%s/combat' % cid)
                if view.get('current_team') == 'hero':
                    return
                turn('%s-npc-%d' % (tag, attempt), dict(kind='npc_turn'))
            raise SystemExit('hero turn never arrived')

        def pass_turn(tag):
            turn(tag + '-end', dict(kind='end_turn'))
            hero_turn(tag)

        hero_turn('r172-open')
        shot = turn('r172-shot', dict(kind='attack', target='foe-1'))
        print('turn 1 (выстрел в упор):', shot)

        status, refused = call('POST', '/api/campaigns/%s/combat/command' % cid,
                               dict(request_id='r172-step', kind='move',
                                    position=Position(x=2, y=1).model_dump()))
        print('turn 2 (проход через занятую клетку):', status, refused)
        assert status == 409, (status, refused)

        pass_turn('r172-a')
        store.record_scenario(
            cid, 'r172-hide', 'Противник становится невидимым',
            lambda state: (state['combat']['actors'][1]['stats']['conditions'].append('invisible')
                           if 'invisible' not in state['combat']['actors'][1]['stats']['conditions']
                           else None,
                           [dict(type='scripted', text='Противник становится невидимым.')])[1])
        hero_turn('r172-hidden-open')
        hidden = turn('r172-hidden', dict(kind='attack', target='foe-1'))
        print('turn 3 (атака по невидимому):', hidden)

        assert 'помеха: рядом враг' in shot, shot
        assert 'd20' in shot
        assert 'цель не видна' in hidden and 'помеха' in hidden, hidden
        assert 'завершить перемещение' in refused['detail'], refused
        print('OK: три хода по HTTP на порту %d, офлайн-мок, без внешних вызовов' % PORT)
    finally:
        server.terminate()
        server.join(timeout=10)


if __name__ == '__main__':
    main()
