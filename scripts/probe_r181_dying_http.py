"""r181 live run: ноль хитов, спасброски от смерти, нокаут и Временные хиты по HTTP.

Приложение поднимается на 127.0.0.1:19218 с `CommandMockLLM` (офлайн-мок, ни
одного платного вызова), кампания живёт в `runtime/r181-probe`. Каждый шаг —
POST на тот же маршрут, что использует экран боя (`/combat/command`), поэтому
отчёт показывает то, что видит игрок, а не то, что умеет класс.

Сценарий:
1. Временные хиты: 5, затем 8 «новыми», затем 4 «оставить старые» — сложения нет.
2. Нокаут: рукопашная атака оставляет противнику 1 Хит и Бессознательность.
3. Герой получает урон, опускающий Хиты до 0: он Бессознательный, Сбитый с ног,
   и с начала своего хода бросает Спасброски от смерти.
4. Провал/успех виден в тексте ответа; три провала останавливают бой на решении
   игрока (`pending_death`) — это и есть «смерть считается, а не объявляется».

Порт 19215 (живой стенд владельца) не трогается.
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

from solo.combat import CombatEngine  # noqa: E402
from solo.combat_service import CombatService, StartEncounter  # noqa: E402
from solo.contracts import Hero  # noqa: E402
from solo.geometry import Position, rectangular_map  # noqa: E402
from solo.storage import Store  # noqa: E402

PORT = 19218
BASE = 'http://127.0.0.1:%d' % PORT
STORE = ROOT / 'runtime' / 'r181-probe' / 'dying-http.sqlite3'


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
    campaign = store.create('r181 · смерть и состояния',
                            Hero(name='Герой', hp=12, max_hp=12, weapon_proficiencies=['martial']))
    cid = campaign['id']

    server = multiprocessing.Process(target=serve, args=(str(STORE), PORT), daemon=True)
    server.start()
    try:
        wait_ready()

        status, started = call('POST', '/api/campaigns/%s/combat/start' % cid, dict(
            request_id='r181-start', confirm=True, hero_weapon='longsword',
            imported_map=rectangular_map(12, 10).model_dump(),
            hero_position=Position(x=1, y=1).model_dump(),
            enemy_position=Position(x=2, y=1).model_dump()))
        assert status == 200, (status, started)
        print('start:', started['results'][0]['text'])

        def command(request_id, body, expect=200):
            status, payload = call('POST', '/api/campaigns/%s/combat/command' % cid,
                                   dict(request_id=request_id, **body))
            assert status == expect, (request_id, status, payload)
            return payload['results'][0]['text'] if status == 200 else payload['detail']

        def view():
            status, payload = call('GET', '/api/campaigns/%s/combat' % cid)
            assert status == 200, (status, payload)
            return payload

        def hero_turn(tag):
            # Инициатива брошена, поэтому первый ход не обязательно за героем.
            for attempt in range(6):
                current = view()
                if current.get('current_team') == 'hero':
                    return
                command('%s-npc-%d' % (tag, attempt), dict(kind='npc_turn'))
            raise SystemExit('hero turn never arrived')

        def hero_block():
            for actor in view()['actors']:
                if actor['team'] == 'hero':
                    return actor
            raise SystemExit('hero missing from the view')

        hero_turn('r181-open')

        # 1. Временные хиты: 5 → 8 новыми → 4 со старыми.
        print('temp 1:', command('r181-temp1', dict(kind='temp_hp', amount=5)))
        print('temp 2:', command('r181-temp2', dict(kind='temp_hp', amount=8, keep='new')))
        print('temp 3:', command('r181-temp3', dict(kind='temp_hp', amount=4, keep='existing')))
        print('view temp_hp:', hero_block()['temp_hp'], '| bloodied:', hero_block()['bloodied'])

        # 2. Нокаут: противник получает смертельную рукопашную атаку с нелетальным выбором.
        def knock_foe(state):
            engine = CombatEngine(state)
            foe = next(a for a in engine.actors.values() if a.team == 'enemy')
            outcome = engine.damage(foe, 999, 'bludgeoning', nonlethal=True)
            engine.flush()
            return [dict(type='scripted', text='Противник нокаутирован: %s' % outcome['text'])]

        store.record_scenario(cid, 'r181-knockout', 'Нелетальный удар по противнику', knock_foe)
        for actor in view()['actors']:
            if actor['team'] == 'enemy':
                print('нокаут: hp %s, состояния %s, жив %s' % (actor['hp'], actor['conditions'], not actor['dead']))

        # 3. Герой падает до 0 Хитов: урон, который ровно съедает оставшиеся Хиты
        #    (временные уже израсходованы первыми).
        def drop_hero(state):
            engine = CombatEngine(state)
            hero = next(a for a in engine.actors.values() if a.team == 'hero')
            engine.damage(hero, hero.stats.hp + hero.temp_hp, 'slashing')
            engine.flush()
            return [dict(type='scripted', text='Герой получает урон, опускающий его Хиты до 0.')]

        store.record_scenario(cid, 'r181-zero', 'Удар, опускающий героя до 0 Хитов', drop_hero)
        hero_state = hero_block()
        print('герой: hp %s, состояния %s, dying %s, спасброски %s' % (
            hero_state['hp'], hero_state['conditions'], hero_state.get('dying'), hero_state.get('death_saves')))

        # 4. Спасброски от смерти до исхода: они бросаются в начале хода героя.
        for attempt in range(10):
            state = view(); hero_state = hero_block()
            if state.get('pending_death') or hero_state.get('dead'):
                print('исход:', json.dumps(state.get('pending_death'), ensure_ascii=False))
                break
            if state.get('current_team') != 'hero':
                status, payload = call('POST', '/api/campaigns/%s/combat/command' % cid,
                                       dict(request_id='r181-npc-%d' % attempt, kind='npc_turn'))
                if status != 200:
                    print('ход противника остановлен: HTTP %s %s' % (status, payload.get('detail')))
                    break
                print('ход противника:', payload['results'][0]['text'])
                continue
            if hero_state['hp'] > 0:
                print('исход: герой вернулся с %s Хитом, состояния %s' % (hero_state['hp'], hero_state['conditions']))
                break
            status, payload = call('POST', '/api/campaigns/%s/combat/command' % cid,
                                   dict(request_id='r181-end-%d' % attempt, kind='end_turn'))
            if status != 200:
                print('ход героя остановлен: HTTP %s %s' % (status, payload.get('detail')))
                break
            print('ход героя:', payload['results'][0]['text'])
    finally:
        server.terminate()
        server.join(timeout=10)


if __name__ == '__main__':
    main()
