"""r220: проба игрового экрана по HTTP и браузеру — черновик кампании и ходы.

Первый шаг: создаёт кампанию Nat20 на своём профиле и играет несколько ходов,
чтобы лента содержала реплику игрока, ответ мастера и пометку броска.
Печатает id кампании и версии — их берёт браузерный шаг.
"""
import argparse
import http.client
import json
import sys


def call(port, method, path, payload=None, timeout=180):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'Content-Type': 'application/json'} if body else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None)
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=19333)
    parser.add_argument('--turns', type=int, default=3)
    parser.add_argument('--campaign', default='')
    args = parser.parse_args()
    port = args.port

    if args.campaign:
        campaign = args.campaign
    else:
        status, created = call(port, 'POST', '/api/nat20/campaigns',
                               {'name': 'Тайна трёх сосен', 'scene': 'lantern-post'})
        if status != 200:
            print('кампания не создана:', status, str(created)[:300])
            return 1
        campaign = created['id']

    status, current = call(port, 'GET', '/api/campaigns/' + campaign)
    version = current['version']
    turns = ['проверяю внимательность', 'описываю трактир', 'говорю с Лада'][:args.turns]
    for number, text in enumerate(turns, 1):
        status, packet = call(port, 'POST', '/api/campaigns/%s/command-turn' % campaign,
                              {'request_id': 'r220-%03d' % number, 'text': text,
                               'expected_version': version})
        if status != 200:
            print('ход %d отказал: %d %s' % (number, status, str(packet)[:300]))
            return 1
        version = packet['state_version']
        print('ход %d: %s, версия %s, реплика %d знаков, ответ %d знаков' % (
            number, packet.get('status'), version, len(text), len(packet.get('narrative') or '')))
    print('кампания', campaign, 'версия', version)
    return 0


if __name__ == '__main__':
    sys.exit(main())
