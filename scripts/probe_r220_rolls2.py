"""r220: где лежит бросок в данных — какие поля есть у события и у экрана.

Печатает ключи записи журнала и записи `screen['events']` целиком, чтобы
плашка броска строилась из структуры движка, а не разбором прозы.
"""
import argparse
import http.client
import json


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
    parser.add_argument('--campaign', required=True)
    args = parser.parse_args()
    status, current = call(args.port, 'GET', '/api/campaigns/' + args.campaign)
    history = current.get('history') or []
    print('история', status, 'записей', len(history))
    for event in history:
        print('--- ключи события:', sorted(event))
    last = history[-1] if history else {}
    print('последнее событие целиком:')
    print(json.dumps(last, ensure_ascii=False, indent=1)[:3000])
    screen_status, screen = call(args.port, 'GET', '/api/campaigns/%s/screen' % args.campaign)
    print('экран', screen_status)
    for event in (screen or {}).get('events') or []:
        print('--- screen event:', json.dumps(event, ensure_ascii=False)[:800])
    print('command_turn:', json.dumps((screen.get('snapshot') or {}).get('command_turn'), ensure_ascii=False)[:800])
    print('commands:', json.dumps((screen or {}).get('commands'), ensure_ascii=False)[:600])
    print('hero из состояния кампании:', json.dumps((current.get('state') or {}).get('hero'), ensure_ascii=False)[:900])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
