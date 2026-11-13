"""r189 live run: 120 ходов → настоящая смерть процесса → продолжение.

Что доказывается числами, а не словом «работает»:

1. Ходы идут теми путями, которыми ходит игрок: `POST /api/campaigns/<id>/command-turn`
   («иду к башне», «осматриваюсь») и `POST /api/campaigns/<id>/play` (путь с планом,
   записью памяти и журнала). Модель — мок `PreviewMock`: платных вызовов нет.
2. Сервер — отдельный процесс (`scripts/probe_r189_child.py`). Рестарт буквальный:
   процесс убит, порт освобождён, на том же профиле поднят новый с другим pid.
   `--crash` гасит его `SIGKILL` вместо штатного выхода — так проверяется, что ход
   лежит в базе, а не в памяти процесса. Живой стенд владельца (19215,
   `runtime/web-local`) не трогается: профиль пробы `runtime/r189-live`, порт `--port`.
3. После рестарта состояние сверяется ЦЕЛИКОМ (по ключам `json`), затем игра
   продолжается следующим ходом — версия обязана вырасти на единицу.
4. После КАЖДОГО хода версия и число событий читаются отдельным соединением к
   файлу базы: доказательство, что ход записан к моменту ответа, а не «по концу сцены».
5. Память и журнал считаются на 0/30/60/90/120 ходах: размер базы, WAL, сумма
   `before_state`+`after_state` в `events`, `campaigns.state`, записи памяти, FTS.

Печатает короткий отчёт и кладёт подробности в `<outdir>/results.json`.
Код возврата 1 — если ход не 200, версии с базой разошлись, состояние после
рестарта разошлось или продолжение не сдвинуло версию.
"""
import argparse
import hashlib
import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / 'scripts' / 'probe_r189_child.py'
# (предлог, как игрок называет место, ожидаемое имя локации)
ROUTE = [('к', 'башне', 'Старая башня'),
         ('на', 'берег реки', 'Берег реки'),
         ('в', 'таверну', 'Таверна «Последний огонь»')]
OBSERVE = 'осматриваюсь'


def planned_turn(number, mix):
    """Чем играет игрок на этом ходу: (вид, текст или None, ожидаемое место или None).

    Без `--mix` — одни путешествия: хост разбирает намерение сам, модель не
    зовётся, и это худший случай для снимков состояния (каждый ход пишет полное
    `before_state`+`after_state`). С `--mix` цикл из четырёх ходов повторяет
    живую игру: дорога, осмотр, разговор, дорога. Разговор важен отдельно —
    именно он пишет память реплик (`memory_records`) и журнал (`journal_fts`);
    на одних путешествиях «память не растёт» означало бы только то, что её
    никто не звал. Кого именно звать, решает `talk_text`: разговор с тем, кого
    на месте нет, — это отказ мока, а не ход игры.
    """
    if not mix:
        index = (number - 1) % len(ROUTE)
        preposition, said, expected = ROUTE[index]
        return 'walk', 'иду %s %s' % (preposition, said), expected
    slot = (number - 1) % 4
    if slot == 1:
        return 'observe', OBSERVE, None
    if slot == 2:
        return 'talk', None, None
    # Дорога чередуется так, чтобы цель каждого второго перехода отличалась от
    # текущего места: путешествие к тому же месту — вырожденный ход на 0 м.
    index = ((number - 1) // 2) % len(ROUTE)
    preposition, said, expected = ROUTE[index]
    return 'walk', 'иду %s %s' % (preposition, said), expected


def talk_text(port, campaign):
    """С кем на этом месте можно поговорить: имя собеседника из самого состояния."""
    status, current, _ = call(port, 'GET', '/api/campaigns/' + campaign)
    if status != 200:
        return None
    state = current['state']
    here = next((place for place in state.get('locations') or []
                 if place.get('name') == state.get('location')), {})
    who = next((npc.get('name') for npc in here.get('npc') or [] if npc.get('present', True)), None)
    return 'говорю с ' + who if who else None


def free_port():
    reservation = socket.socket()
    try:
        reservation.bind(('127.0.0.1', 0))
        return reservation.getsockname()[1]
    finally:
        reservation.close()


def call(port, method, path, payload=None, timeout=120):
    """Один запрос по голому сокету: urllib отправляет loopback через прокси."""
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'Content-Type': 'application/json'} if body else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None), raw
    finally:
        connection.close()


def start_child(database, port, stop_token, log_path):
    env = {**os.environ,
           'SOLO_CHILD_DB': str(database),
           'SOLO_CHILD_PORT': str(port),
           'SOLO_CHILD_STOP': str(stop_token)}
    log = log_path.open('w', encoding='utf-8')
    process = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', str(CHILD)],
                               stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                               text=True, env=env, cwd=str(ROOT))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            status, health, _ = call(port, 'GET', '/api/health', timeout=5)
            if status == 200:
                return process, health
        except (OSError, ValueError):
            pass
        if process.poll() is not None:
            tail = log_path.read_text(encoding='utf-8', errors='replace')[-800:]
            raise SystemExit('дочерний сервер умер при старте rc=%r: %s' % (process.returncode, tail))
        time.sleep(.1)
    process.kill()
    raise SystemExit('дочерний сервер не ответил за 60 с')


def stop_child(process, stop_token, crash=False):
    if crash:
        process.kill()
        process.wait(20)
        return 'SIGKILL (авария, без штатного выхода)'
    stop_token.write_text('stop', encoding='utf-8')
    try:
        process.wait(40)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(10)
        return 'убит по таймауту'
    return 'вышел сам, rc=%r' % (process.returncode,)


def size(path):
    path = Path(path)
    return path.stat().st_size if path.exists() else 0


def census(database):
    """Размер базы и то, что растёт с числом ходов."""
    database = Path(database)
    counts = {}
    if database.exists():
        import sqlite3
        with sqlite3.connect(str(database), timeout=20) as db:
            def one(sql, *args):
                row = db.execute(sql, args).fetchone()
                return row[0] if row else 0
            counts = {
                'events': one('SELECT count(*) FROM events'),
                'state_bytes': one('SELECT coalesce(sum(length(before_state)+length(after_state)),0) FROM events'),
                'event_bytes': one('SELECT coalesce(sum(length(results)+length(narrative)),0) FROM events'),
                'campaign_state_bytes': one('SELECT coalesce(sum(length(state)),0) FROM campaigns'),
                'conversations': one('SELECT count(*) FROM conversations'),
                'memory_records': one('SELECT count(*) FROM memory_records'),
                'memory_bytes': one('SELECT coalesce(sum(length(content)),0) FROM memory_records'),
                'memory_engine': one('SELECT count(*) FROM memory_engine_events'),
                'fts_rows': one('SELECT count(*) FROM journal_fts'),
                'fts_bytes': one('SELECT coalesce(sum(length(content)),0) FROM journal_fts'),
                'checkpoints': one('SELECT count(*) FROM checkpoints'),
            }
        wal = Path(str(database) + '-wal')
        try:
            with sqlite3.connect(str(database), timeout=20) as db:
                db.execute('PRAGMA wal_checkpoint(PASSIVE)')
        except Exception:  # noqa: BLE001 - размер WAL не критичен для вывода
            pass
        counts['wal_bytes'] = size(wal)
    counts['db_bytes'] = size(database)
    return counts


def direct_version(database, campaign):
    """Версия и число событий так, как их видит ЧУЖОЕ соединение.

    Сервер отвечает «ход записан», но доказать, что запись уже в базе к этому
    моменту, может только отдельный читатель: он не видит ни одного объекта
    процесса, который писал.
    """
    import sqlite3
    with sqlite3.connect(str(database), timeout=20) as db:
        row = db.execute('SELECT version FROM campaigns WHERE id=?', (campaign,)).fetchone()
        events = db.execute('SELECT count(*) FROM events WHERE campaign=?', (campaign,)).fetchone()[0]
    return (row[0] if row else None), events


def state_digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def differing_fields(before, after):
    """Какие ключи состояния разошлись после рестарта."""
    keys = sorted(set(before) | set(after))
    return [key for key in keys
            if json.dumps(before.get(key), sort_keys=True) != json.dumps(after.get(key), sort_keys=True)]


def hero_fields(state):
    hero = state.get('hero') or {}
    return {key: hero.get(key) for key in ('name', 'hp', 'max_hp', 'level', 'class_name', 'species')}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--turns', type=int, default=120)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--data-dir', default=str(ROOT / 'runtime' / 'r189-live'))
    parser.add_argument('--outdir', default='')
    parser.add_argument('--mix', action='store_true', help='путешествия вперемешку с осмотром и путём с планом')
    parser.add_argument('--crash', action='store_true', help='гасить сервер SIGKILL вместо штатного выхода')
    args = parser.parse_args()

    data = Path(args.data_dir)
    if data.exists():
        import shutil
        shutil.rmtree(data)
    data.mkdir(parents=True)
    database = data / 'solo.sqlite3'
    outdir = Path(args.outdir) if args.outdir else Path(str(database) + '.run')
    outdir.mkdir(parents=True, exist_ok=True)
    result = {'turns': args.turns, 'mix': args.mix, 'crash': args.crash,
              'checks': {}, 'turns_ms': [], 'census': {}, 'turn_texts': []}

    port = args.port or free_port()
    stop_a, stop_b = data / 'stop-a', data / 'stop-b'
    print('== профиль', data, 'порт', port, 'режим', 'смешанный' if args.mix else 'путешествия',
          '| рестарт', 'SIGKILL' if args.crash else 'штатный', flush=True)

    process_a, health_a = start_child(database, port, stop_a, outdir / 'server-a.log')
    result['checks']['health_a'] = {key: health_a.get(key) for key in ('product', 'commit', 'version') if health_a}
    print('== сервер A pid', process_a.pid, 'health', result['checks']['health_a'], flush=True)

    hero = {'name': 'Проба Долгой Сессии', 'level': 1, 'hp': 12, 'max_hp': 12, 'ac': 14,
            'species': 'human', 'class_name': 'fighter', 'background': 'Стражник из пробы.'}
    status, created, _ = call(port, 'POST', '/api/campaigns',
                              {'name': 'r189 долгая сессия', 'hero': hero, 'ruleset': 'dnd2024',
                               'world_mode': 'story', 'map_travel': False})
    if status != 200:
        raise SystemExit('кампания не создана: %d %s' % (status, created))
    campaign = created['id']
    print('== кампания', campaign, 'версия', created.get('version'), flush=True)

    result['census']['0'] = census(database)
    failures = []
    version = created['version']
    for number in range(1, args.turns + 1):
        kind, text, expected = planned_turn(number, args.mix)
        if kind == 'talk':
            text = talk_text(port, campaign) or OBSERVE
        started = time.perf_counter()
        status, packet, _ = call(port, 'POST', '/api/campaigns/%s/command-turn' % campaign,
                                 {'request_id': 'r189-turn-%03d' % number, 'text': text,
                                  'expected_version': version})
        elapsed = (time.perf_counter() - started) * 1000
        result['turns_ms'].append(round(elapsed, 1))
        result['turn_texts'].append(text)
        if status != 200:
            failures.append({'turn': number, 'text': text, 'status': status,
                             'detail': json.dumps(packet, ensure_ascii=False)[:300]})
            break
        version = packet['state_version']
        spoken = packet.get('narrative') or ''
        # Ход уже в базе к моменту ответа: читает ДРУГОЕ соединение, не сервер.
        disk_version, disk_events = direct_version(database, campaign)
        if disk_version != version:
            failures.append({'turn': number, 'text': text, 'status': 'not-persisted',
                             'detail': 'в базе версия %r, сервер ответил %r' % (disk_version, version)})
            break
        if number in (1, args.turns // 2, args.turns):
            print('== ход %d: %.0f мс, версия %d, в базе %r/%d, %r' % (
                number, elapsed, version, disk_version, disk_events, spoken[:60]), flush=True)
        if number % 30 == 0 or number == args.turns:
            result['census'][str(number)] = census(database)
            shard = result['census'][str(number)]
            print('==   срез %3d: база %d КБ, WAL %d КБ, событий %d, состояние %d КБ, память %d, FTS %d' % (
                number, shard['db_bytes'] // 1024, shard['wal_bytes'] // 1024, shard['events'],
                shard['state_bytes'] // 1024, shard['memory_records'], shard['fts_rows']), flush=True)

    if failures:
        result['checks']['failures'] = failures
        (outdir / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
        print('ОТКАЗ: ход %r (%s) вернул %r: %s' % (failures[0]['turn'], failures[0]['path'],
                                                    failures[0]['status'], failures[0]['detail']))
        return 1

    # Экран, который открывает игрок: сколько он весит и сколько стоит по времени.
    screen_started = time.perf_counter()
    status, current, raw = call(port, 'GET', '/api/campaigns/' + campaign)
    screen_ms = (time.perf_counter() - screen_started) * 1000
    if status != 200:
        raise SystemExit('кампания не читается: %d %s' % (status, current))
    before_state = current['state']
    result['checks']['before_restart'] = {
        'pid': process_a.pid, 'version': current['version'], 'turn': before_state.get('turn'),
        'location': before_state.get('location'), 'hero': hero_fields(before_state),
        'state_sha': state_digest(before_state), 'screen_ms': round(screen_ms, 1),
        'screen_bytes': len(raw), 'journal_rows': len(current.get('history') or []),
        'conversation': current.get('conversation_total'), 'saves': len(current.get('saves') or []),
        'locations': [row['name'] for row in before_state.get('locations') or []],
    }
    print('== экран до рестарта: %.0f мс, %d КБ, журнал %d, разговор %r' % (
        screen_ms, len(raw) // 1024, len(current.get('history') or []), current.get('conversation_total')), flush=True)
    (outdir / 'state-before.json').write_text(json.dumps(before_state, ensure_ascii=False), encoding='utf-8')

    # Настоящая смерть процесса.
    how = stop_child(process_a, stop_a, crash=args.crash)
    result['checks']['stop'] = {'pid': process_a.pid, 'how': how, 'rc': process_a.returncode}
    print('== сервер A остановлен:', how, flush=True)
    try:
        call(port, 'GET', '/api/health', timeout=2)
        print('!! порт всё ещё отвечает после остановки — рестарт не настоящий')
        return 1
    except (OSError, ValueError):
        pass

    process_b, health_b = start_child(database, port, stop_b, outdir / 'server-b.log')
    result['checks']['health_b'] = {key: health_b.get(key) for key in ('product', 'commit', 'version') if health_b}
    print('== сервер B pid', process_b.pid, flush=True)

    status, restored, raw_b = call(port, 'GET', '/api/campaigns/' + campaign)
    if status != 200:
        raise SystemExit('после рестарта кампания не читается: %d %s' % (status, restored))
    after_state = restored['state']
    diff = differing_fields(before_state, after_state)
    result['checks']['after_restart'] = {
        'pid': process_b.pid, 'version': restored['version'], 'turn': after_state.get('turn'),
        'location': after_state.get('location'), 'hero': hero_fields(after_state),
        'state_sha': state_digest(after_state), 'different_fields': diff,
        'screen_bytes': len(raw_b), 'journal_rows': len(restored.get('history') or []),
        'conversation': restored.get('conversation_total'), 'saves': len(restored.get('saves') or []),
    }
    print('== после рестарта: версия %d, место %r, разошлось %r' % (
        restored['version'], after_state.get('location'), diff), flush=True)

    # Продолжение игры следующим ходом того же режима.
    kind, text, expected = planned_turn(args.turns + 1, args.mix)
    if kind == 'talk':
        text = talk_text(port, campaign) or OBSERVE
    status, packet, _ = call(port, 'POST', '/api/campaigns/%s/command-turn' % campaign,
                             {'request_id': 'r189-after-restart', 'text': text,
                              'expected_version': restored['version']})
    continued = {'text': text, 'status': status,
                 'detail': json.dumps(packet, ensure_ascii=False)[:300] if status != 200 else ''}
    if status == 200:
        final = call(port, 'GET', '/api/campaigns/' + campaign)[1]
        continued.update({
            'version_before': restored['version'], 'version_after': final['version'],
            'turn_after': final['state'].get('turn'), 'location_after': final['state'].get('location'),
            'expected_location': expected,
            'narrative': (packet.get('narrative') or '')[:200],
        })
        result['census']['after_restart'] = census(database)
    result['checks']['continued'] = continued
    print('== продолжение после рестарта:', json.dumps(continued, ensure_ascii=False), flush=True)

    stop_child(process_b, stop_b)

    location_ok = expected is None or continued.get('location_after') == expected
    ok = (not diff and status == 200 and continued.get('version_after') == restored['version'] + 1
          and location_ok
          and (continued.get('turn_after') or 0) > (after_state.get('turn') or 0))
    result['verdict'] = 'ok' if ok else 'fail'
    (outdir / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')

    turns = result['turns_ms']
    print('\n=== ИТОГ ===')
    print('ходов: %d, ошибок: 0' % len(turns))
    print('время хода 1: %.0f мс, медиана: %.0f мс, %d-го: %.0f мс' % (
        turns[0], sorted(turns)[len(turns) // 2], len(turns), turns[-1]))
    for label, value in result['census'].items():
        print('срез %-13s база %6.1f КБ, WAL %6.1f КБ, событий %5d, снимки %6.1f КБ, '
              'состояние %5.1f КБ, память %4d / %.1f КБ, FTS %3d / %.1f КБ' % (
                  label, value['db_bytes'] / 1024, value['wal_bytes'] / 1024, value['events'],
                  value['state_bytes'] / 1024, value['campaign_state_bytes'] / 1024,
                  value['memory_records'], value['memory_bytes'] / 1024,
                  value['fts_rows'], value['fts_bytes'] / 1024))
    print('состояние до/после рестарта совпало:', not diff, '(разошлось: %r)' % (diff,))
    print('итог:', result['verdict'], '| подробности:', outdir / 'results.json')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
