"""r176 live run: темп путешествия на настоящем HTTP, свой порт, офлайн-мок.

Приложение поднимается на 127.0.0.1:19218 (порт 19215 — живой стенд владельца —
не трогается) с ``CommandMockLLM``: ни сети, ни платных вызовов. Реплики идут
тем же маршрутом, что у игрока: ``POST /api/campaigns/<id>/play``.

Ходы:
1. «идём быстрым темпом до вечера»   -> событие с посчитанными милями и помехами;
2. «идём быстрым темпом форсированным маршем 9 часов» -> спасбросок Телосложения
   и степень истощения;
3. «прячусь»                          -> отказ: укрытие не объявлено;
4. «ищу ловушку»                      -> отказ: область поиска не объявлена;
5. «беру груз 500 фунтов»             -> отказ: перегруз с числами;
6. состояние кампании                 -> темп и истощение записаны в state.

База лежит в ``runtime/r176-probe`` и удаляется при старте.
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

from solo.contracts import Hero  # noqa: E402
from solo.rulesets import BUILTINS  # noqa: E402
from solo.storage import Store  # noqa: E402

PORT = 19218
BASE = 'http://127.0.0.1:%d' % PORT
STORE = ROOT / 'runtime' / 'r176-probe' / 'travel-http.sqlite3'


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
    hero = Hero(name='Путник', abilities=dict(strength=15, dexterity=14, constitution=14,
                                              intelligence=10, wisdom=12, charisma=10))
    cid = store.create('r176 · дорога', hero, BUILTINS['dnd2024'])['id']

    server = multiprocessing.Process(target=serve, args=(str(STORE), PORT), daemon=True)
    server.start()
    try:
        wait_ready()

        def say(request_id, text):
            status, payload = call('POST', '/api/campaigns/%s/play' % cid,
                                   dict(request_id=request_id, text=text))
            assert status == 200, (request_id, status, payload)
            if payload['kind'] == 'event':
                return 'event', payload['event']['results'][0]
            return 'reply', payload.get('reply')

        kind, result = say('r176-pace', 'идём быстрым темпом до вечера')
        assert kind == 'event' and result['kind'] == 'travel_pace', (kind, result)
        print('1. хост посчитал:', result['text'])
        print('   поля события:', {key: result[key] for key in
                              ('pace', 'hours', 'miles', 'miles_per_hour', 'feet_per_minute')})

        kind, result = say('r176-march', 'идём быстрым темпом форсированным маршем 9 часов')
        assert kind == 'event' and result['forced_march'], (kind, result)
        print('2. хост посчитал:', result['text'])
        print('   спасброски:', result['forced_march']['slots'])

        for request_id, phrase, expected in (
                ('r176-hide', 'прячусь', 'укрытие'),
                ('r176-search', 'ищу ловушку', 'не объявлена'),
                ('r176-load', 'беру груз 500 фунтов', 'Перегруз')):
            kind, reply = say(request_id, phrase)
            assert kind == 'reply' and expected in reply, (phrase, kind, reply)
            print('3. игроку сказано на "%s": %s' % (phrase, reply))

        status, campaign = call('GET', '/api/campaigns/%s' % cid)
        assert status == 200
        state = campaign['state']
        print('4. состояние кампании: темп =', state['travel']['pace'],
              '| часы =', state['travel']['hours'],
              '| мили =', state['travel']['miles'],
              '| истощение героя =', state['hero']['exhaustion'])
        print('5. событие в истории:', len(call('GET', '/api/campaigns/%s' % cid)[1]['history']), 'шт.')
    finally:
        server.terminate()
        server.join(timeout=10)


if __name__ == '__main__':
    main()
