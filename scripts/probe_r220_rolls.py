"""r220: что именно движок отдаёт в ленту — форма записи броска.

Печатает по каждому событию журнала `results` целиком (это список записей),
чтобы плашка в ленте строилась из данных движка, а не из догадок об их форме.
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
    print('кампания', status)
    for event in current.get('history') or []:
        print('---', event.get('id'), event.get('request_id'))
        print('    results:', json.dumps(event.get('results'), ensure_ascii=False)[:1200])
        print('    narrative_data:', json.dumps(event.get('narrative_data'), ensure_ascii=False)[:300])
    screen_status, screen = call(args.port, 'GET', '/api/campaigns/%s/screen' % args.campaign)
    print('screen', screen_status, 'keys', sorted(screen or {}))
    snap = (screen or {}).get('snapshot') or {}
    print('snapshot keys', sorted(snap))
    print('last_roll', json.dumps(snap.get('last_roll'), ensure_ascii=False)[:600])
    print('hero', json.dumps((screen or {}).get('hero'), ensure_ascii=False)[:500])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
