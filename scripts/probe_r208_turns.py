"""r208: сколько ждёт игрок на первом и на сотом ходу — и из чего это время.

Проба, а не тест: поднимает СВОЙ инстанс (отдельный каталог профиля, свободный
порт, офлайн-мок `PreviewMock`, платных вызовов нет), играет кампанию Nat20
120 ходами и меряет каждый ход двумя способами:

* **глазами игрока** — `time.perf_counter()` вокруг HTTP-запроса
  `POST /api/campaigns/<id>/command-turn`;
* **изнутри сервера** — дочерний процесс `scripts/probe_r208_child.py` пишет в
  JSONL время каждого слоя хода (разбор реплики, пакет контекста, вызов
  движка, подпроцесс воркера, запись журнала, память, проекция экрана).

Реплики цикла — те, что реально доходят до движка в сцене «Письмо у старого
фонаря»: проверка, дорога, проверка, дорога, проверка, разговор. Ни один ход
не вырожденный: каждый пишет событие и снимок состояния.

После каждого хода отдельное соединение к файлу профиля читает версию, число
событий, записей памяти, строк FTS и размеры — чтобы «время выросло» можно
было сопоставить с «данные выросли».

Печатает таблицу «ход → секунды» по точкам 1/10/50/100/120 (медиана по
прогонам) и сводку по слоям; подробности — в `<outdir>/results.json`.
Код возврата 1 — если ход не 200, запись слоёв потерялась или версия в базе
разошлась с ответом сервера.
"""
import argparse
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHILD = ROOT / 'scripts' / 'probe_r208_child.py'
CHECKPOINTS = (1, 10, 50, 100, 120)
# Проверки (единственный вид хода, который зовёт движок) стоят на нечётных
# ходах цикла: 1, 3, 5, 7, … Поэтому «ход 10» — это дорога, и расти по нему
# нельзя судить. Точки ниже падают ровно на проверки.
CHECK_TURNS = (1, 11, 51, 99, 119)
# Полосы, по которым видно рост: в каждой десятки одинаковых ходов, а не один —
# иначе разброс в 0,6 с принимается за деградацию.
BANDS = ((1, 10), (11, 50), (51, 100), (101, 120))
HOME = 'Домик смотрительницы'
YARD = 'Двор фонаря'


def planned_turn(number):
    """Что игрок делает на этом ходу: цикл из шести реплик живой игры.

    Три проверки на цикл: они — единственные ходы, которые зовут движок в
    подпроцессе, то есть самая дорогая и самая частая механика вечера.
    Дорога и разговор зовут только хост, поэтому их видно отдельной строкой,
    а не в среднем по всем ходам.
    """
    slot = (number - 1) % 6
    if slot == 0:
        return 'проверяю внимательность'
    if slot == 1:
        return 'иду во двор фонаря'
    if slot == 2:
        return 'проверяю следы'
    if slot == 3:
        return 'иду в домик смотрительницы'
    if slot == 4:
        return 'проверяю внимательность'
    return 'говорю с Лада'


def kind_of(text):
    low = text.casefold()
    if low.startswith(('проверяю', 'ищу', 'проверка', 'проверить')):
        return 'check'
    if low.startswith(('иду', 'перехожу', 'двигаюсь')):
        return 'travel'
    if low.startswith(('говорю', 'поговор', 'расспраш', 'привет')):
        return 'talk'
    if low.startswith(('осматр', 'огляд', 'опиши')):
        return 'observe'
    return 'other'


def free_port():
    reservation = socket.socket()
    try:
        reservation.bind(('127.0.0.1', 0))
        return reservation.getsockname()[1]
    finally:
        reservation.close()


def call(port, method, path, payload=None, timeout=180):
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


def start_child(database, port, stop_token, log_path, times_path):
    environment = {**os.environ,
                   'SOLO_CHILD_DB': str(database),
                   'SOLO_CHILD_PORT': str(port),
                   'SOLO_CHILD_STOP': str(stop_token),
                   'SOLO_R208_TIMES': str(times_path)}
    log = log_path.open('w', encoding='utf-8')
    process = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', str(CHILD)],
                               stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                               text=True, env=environment, cwd=str(ROOT))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            status, health, _ = call(port, 'GET', '/api/health', timeout=5)
            if status == 200:
                return process, health
        except (OSError, ValueError):
            pass
        if process.poll() is not None:
            tail = log_path.read_text(encoding='utf-8', errors='replace')[-1200:]
            raise SystemExit('дочерний сервер умер при старте rc=%r: %s' % (process.returncode, tail))
        time.sleep(.1)
    process.kill()
    raise SystemExit('дочерний сервер не ответил за 90 с')


def stop_child(process, stop_token):
    stop_token.write_text('stop', encoding='utf-8')
    try:
        process.wait(60)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(10)
        return 'убит по таймауту'
    return 'вышел сам, rc=%r' % (process.returncode,)


def census(database, campaign):
    """Что выросло к этому ходу — читает отдельное соединение, не сервер."""
    with sqlite3.connect('file:%s?mode=ro' % database, uri=True, timeout=20) as db:
        def one(sql, *args):
            row = db.execute(sql, args).fetchone()
            return row[0] if row else 0
        version = one('SELECT version FROM campaigns WHERE id=?', campaign)
        events = one('SELECT count(*) FROM events WHERE campaign=?', campaign)
        memories = one('SELECT count(*) FROM memory_records WHERE campaign=?', campaign)
        fts = one('SELECT count(*) FROM journal_fts WHERE campaign=?', campaign)
        state_bytes = one('SELECT coalesce(length(state),0) FROM campaigns WHERE id=?', campaign)
        state_rows = one('SELECT coalesce(sum(length(before_state)+length(after_state)),0) '
                         'FROM events WHERE campaign=?', campaign)
        engine_events = one('SELECT count(*) FROM memory_engine_events WHERE campaign=?', campaign)
    path = Path(database)
    wal = Path(str(database) + '-wal')
    return dict(version=version, events=events, memories=memories, fts=fts,
                state_bytes=state_bytes, snapshot_bytes=state_rows,
                engine_events=engine_events,
                db_bytes=path.stat().st_size if path.exists() else 0,
                wal_bytes=wal.stat().st_size if wal.exists() else 0)


def read_times(times_path, offset):
    """Новые строки замера, дописанные сервером с прошлого чтения."""
    if not times_path.exists():
        return [], offset
    with times_path.open('r', encoding='utf-8') as stream:
        stream.seek(offset)
        raw = stream.read()
        offset = stream.tell()
    return [json.loads(line) for line in raw.splitlines() if line.strip()], offset


def one_run(index, args, data_root):
    data = data_root / ('run%d' % index)
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    database = data / 'solo.sqlite3'
    times_path = data / 'times.jsonl'
    port = args.port or free_port()
    stop_token = data / 'stop'
    process, health = start_child(database, port, stop_token, data / 'server.log', times_path)
    log = {'run': index, 'port': port, 'pid': process.pid,
           'health': {key: health.get(key) for key in ('product', 'commit', 'version') if health},
           'turns': []}
    print('== прогон %d: порт %d, pid %d, сборка %s' % (
        index, port, process.pid, log['health']), flush=True)
    failures = []
    try:
        status, created, _ = call(port, 'POST', '/api/nat20/campaigns',
                                  {'name': 'r208 долгая игра %d' % index, 'scene': 'lantern-post'})
        if status != 200:
            raise SystemExit('кампания Nat20 не создана: %d %s' % (status, created))
        campaign = created['id']
        status, current, _ = call(port, 'GET', '/api/campaigns/' + campaign)
        version = current['version']
        log['campaign'] = campaign
        log['census_start'] = census(database, campaign)
        offset = 0
        for number in range(1, args.turns + 1):
            text = planned_turn(number)
            started = time.perf_counter()
            status, packet, raw = call(port, 'POST', '/api/campaigns/%s/command-turn' % campaign,
                                       {'request_id': 'r208-%03d' % number, 'text': text,
                                        'expected_version': version})
            client_ms = (time.perf_counter() - started) * 1000
            if status != 200:
                failures.append({'turn': number, 'text': text, 'status': status,
                                 'detail': (packet or raw.decode('utf-8', 'replace'))[:300]})
                break
            version = packet['state_version']
            fresh, offset = read_times(times_path, offset)
            record = fresh[-1] if fresh else {}
            if not record:
                failures.append({'turn': number, 'text': text, 'status': 'no-server-timing',
                                 'detail': 'сервер не записал замер хода'})
                break
            disk = census(database, campaign)
            if disk['version'] != version:
                failures.append({'turn': number, 'text': text, 'status': 'not-persisted',
                                 'detail': 'в базе %r, ответ %r' % (disk['version'], version)})
                break
            record.update(client_ms=round(client_ms, 2), status=packet.get('status'),
                          narrative_bytes=len(packet.get('narrative') or ''),
                          response_bytes=len(raw), census=disk)
            log['turns'].append(record)
            if number in CHECKPOINTS or number % 20 == 0:
                print('   ход %3d %-6s %-30s %6.0f мс (сервер %6.0f) событий %d, память %d, база %d КБ' % (
                    number, record.get('kind'), text[:30], client_ms,
                    record.get('server_ms') or 0, disk['events'], disk['memories'],
                    disk['db_bytes'] // 1024), flush=True)
    finally:
        log['stop'] = stop_child(process, stop_token)
        log['census_end'] = census(database, campaign) if log.get('campaign') else {}
        log['failures'] = failures
    return log


def median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def summarise(runs, checkpoints):
    """Медиана по прогонам в каждой точке: ход → время, и то же по слоям."""
    table = []
    for point in checkpoints:
        sample = [turn for run in runs for turn in run['turns'] if turn['turn'] == point]
        if not sample:
            continue
        layers = {}
        for turn in sample:
            for key, value in (turn.get('layers') or {}).items():
                layers.setdefault(key, []).append(value)
        table.append(dict(
            turn=point,
            kind=sample[0].get('kind'),
            client_ms=median([t['client_ms'] for t in sample]),
            client_spread=[min(t['client_ms'] for t in sample), max(t['client_ms'] for t in sample)],
            server_ms=median([t.get('server_ms') for t in sample]),
            layers={key: median(values) for key, values in layers.items()},
            kind_layers={},
            census=sample[0].get('census'),
        ))
        by_kind = {}
        for turn in sample:
            bucket = by_kind.setdefault(turn.get('kind'), {'client_ms': [], 'layers': {}})
            bucket['client_ms'].append(turn['client_ms'])
            for key, value in (turn.get('layers') or {}).items():
                bucket['layers'].setdefault(key, []).append(value)
        table[-1]['kind_layers'] = {
            kind: dict(count=len(bucket['client_ms']),
                       client_ms=median(bucket['client_ms']),
                       layers={key: median(values) for key, values in bucket['layers'].items()})
            for kind, bucket in by_kind.items()}
    return table


def bands(runs, kinds=('check', 'travel', 'talk')):
    """Рост по полосам: десятки одинаковых ходов в каждой, а не одна точка."""
    rows = []
    for kind in kinds:
        for low, high in BANDS:
            sample = [turn for run in runs for turn in run['turns']
                      if turn.get('kind') == kind and low <= turn['turn'] <= high]
            if not sample:
                continue
            layers = {}
            for turn in sample:
                for key, value in (turn.get('layers') or {}).items():
                    layers.setdefault(key, []).append(value)
            rows.append(dict(kind=kind, low=low, high=high, count=len(sample),
                             client_ms=median([t['client_ms'] for t in sample]),
                             client_min=min(t['client_ms'] for t in sample),
                             client_max=max(t['client_ms'] for t in sample),
                             layers={key: median(values) for key, values in layers.items()}))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--turns', type=int, default=120)
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--data-dir', default=str(ROOT / 'runtime' / 'r208-long'))
    parser.add_argument('--outdir', default='')
    parser.add_argument('--summarise', default='',
                        help='только пересчитать готовый results.json, не играть')
    args = parser.parse_args()

    if args.summarise:
        result = json.loads(Path(args.summarise).read_text(encoding='utf-8'))
        report(result)
        return 0

    data_root = Path(args.data_dir)
    if data_root.exists():
        shutil.rmtree(data_root)
    data_root.mkdir(parents=True)
    outdir = Path(args.outdir) if args.outdir else data_root
    outdir.mkdir(parents=True, exist_ok=True)

    print('== r208: %d прогонов по %d ходов, профиль %s' % (args.runs, args.turns, data_root), flush=True)
    started = time.time()
    runs = []
    for index in range(1, args.runs + 1):
        runs.append(one_run(index, args, data_root))
        if runs[-1]['failures']:
            print('ОТКАЗ прогона %d: %r' % (index, runs[-1]['failures'][0]), flush=True)
            break
    result = dict(turns=args.turns, runs=runs, seconds=round(time.time() - started, 1),
                  checkpoints=summarise(runs, CHECKPOINTS),
                  check_turns=summarise(runs, CHECK_TURNS),
                  bands=bands(runs))
    (outdir / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                         encoding='utf-8')
    report(result)
    print('== подробности:', outdir / 'results.json')
    return 1 if any(run['failures'] for run in runs) else 0


def report(result):
    runs = result['runs']
    # Старый results.json (до появления полос) пересчитывается из самих ходов.
    result = dict(result,
                  check_turns=result.get('check_turns') or summarise(runs, CHECK_TURNS),
                  bands=result.get('bands') or bands(runs))
    print('\n== ХОД -> СЕКУНДЫ (медиана по %d прогонам, разброс min..max)' % len(runs))
    print('%5s %-6s %10s %22s %10s' % ('ход', 'вид', 'клиент, с', 'разброс, с', 'сервер, с'))
    for row in result['checkpoints']:
        print('%5d %-6s %10.2f %22s %10.2f' % (
            row['turn'], row['kind'], (row['client_ms'] or 0) / 1000,
            '%.2f..%.2f' % (row['client_spread'][0] / 1000, row['client_spread'][1] / 1000),
            (row['server_ms'] or 0) / 1000))
    print('\n== ТО ЖЕ НА ПРОВЕРКАХ (настоящий механический ход)')
    for row in result.get('check_turns') or []:
        print('%5d %-6s %10.2f %22s %10.2f  worker %5.0f  context %4.0f' % (
            row['turn'], row['kind'], (row['client_ms'] or 0) / 1000,
            '%.2f..%.2f' % (row['client_spread'][0] / 1000, row['client_spread'][1] / 1000),
            (row['server_ms'] or 0) / 1000,
            (row['layers'] or {}).get('worker') or 0, (row['layers'] or {}).get('context') or 0))

    print('\n== СЛОИ ХОДА, медиана в точке (мс; слои вложены, не складывать)')
    for row in result['checkpoints']:
        layers = row['layers']
        print('ход %3d: ' % row['turn'] + ('  '.join(
            '%s=%.0f' % (key, value) for key, value in sorted(layers.items(), key=lambda kv: -kv[1])
            if value >= 1) or 'слоёв нет'))

    print('\n== РОСТ ПО ПОЛОСАМ (медиана хода этого вида, разброс)')
    for row in result.get('bands') or []:
        layers = row['layers']
        print('%-6s ходы %3d-%3d n=%3d клиент %.3f с (%.2f..%.2f) | %s' % (
            row['kind'], row['low'], row['high'], row['count'], row['client_ms'] / 1000,
            row['client_min'] / 1000, row['client_max'] / 1000,
            ' '.join('%s=%.0f' % (key, value)
                     for key, value in sorted(layers.items(), key=lambda kv: -kv[1])[:5] if value >= 1)))

    print('\n== итог: прогонов %d, ходов %d, отказов %d, за %.0f с' % (
        len(runs), sum(len(run['turns']) for run in runs),
        sum(1 for run in runs if run['failures']), result.get('seconds') or 0))


if __name__ == '__main__':
    raise SystemExit(main())
