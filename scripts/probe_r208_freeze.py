"""r208: что происходит с остальным сервером, пока идёт ход с механикой.

Проба поднимает СВОЙ инстанс (свой профиль, свободный порт, офлайн-мок) и
делает то, что не может увидеть одиночный замер хода: во время хода
«проверяю внимательность» (он зовёт движок в подпроцессе) посылает второй,
посторонний запрос — чтение сцены, которое в покое стоит единицы миллисекунд.
Печатает, сколько этот второй запрос ждал.

Если сервер отдаёт сцену только после конца хода, игрок ждёт не только свой
ход: любая панель, второй экран и проверка живости стоят столько же.

Код возврата 1 — если ход не 200, сцена не создана или второй запрос не
дождался ответа.
"""
import argparse
import json
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_r208_child import ROOT  # noqa: E402  (тот же корень и тот же ребёнок)
from probe_r208_turns import call, free_port, start_child, stop_child  # noqa: E402


def measure(port, campaign, version, kind):
    """Один ход и один посторонний GET во время него."""
    turn = {}

    def play():
        started = time.perf_counter()
        status, packet, _ = call(port, 'POST', '/api/campaigns/%s/command-turn' % campaign,
                                 {'request_id': 'freeze-%s-%f' % (kind, time.time()),
                                  'text': 'проверяю внимательность', 'expected_version': version[0]})
        turn.update(status=status, seconds=time.perf_counter() - started,
                    version=(packet or {}).get('state_version'))
        version[0] = turn['version']

    thread = threading.Thread(target=play)
    thread.start()
    time.sleep(0.30)  # ход уже внутри движка, но ещё не ответил
    started = time.perf_counter()
    status, scene, _ = call(port, 'GET', '/api/campaigns/' + campaign, timeout=180)
    waited = time.perf_counter() - started
    thread.join(180)
    return dict(turn_status=turn.get('status'), turn_seconds=turn.get('seconds'),
                scene_status=status, scene_seconds=waited,
                scene_bytes=len(scene or {}), scene_version=(scene or {}).get('version'))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--reps', type=int, default=3)
    parser.add_argument('--data-dir', default=str(ROOT / 'runtime' / 'r208-freeze'))
    parser.add_argument('--outdir', default='')
    args = parser.parse_args()

    data = Path(args.data_dir)
    if data.exists():
        shutil.rmtree(data)
    data.mkdir(parents=True)
    outdir = Path(args.outdir) if args.outdir else data
    outdir.mkdir(parents=True, exist_ok=True)
    database = data / 'solo.sqlite3'
    port = free_port()
    process, health = start_child(database, port, data / 'stop', data / 'server.log',
                                  data / 'times.jsonl')
    print('== сервер pid %d, порт %d, сборка %s' % (
        process.pid, port, (health or {}).get('commit')), flush=True)
    rows = []
    try:
        status, created, _ = call(port, 'POST', '/api/nat20/campaigns',
                                  {'name': 'r208 заморозка', 'scene': 'lantern-post'})
        if status != 200:
            raise SystemExit('кампания не создана: %d %s' % (status, created))
        campaign = created['id']
        version = [call(port, 'GET', '/api/campaigns/' + campaign)[1]['version']]

        # Покой: та же сцена, без хода рядом.
        quiet = []
        for _ in range(5):
            started = time.perf_counter()
            call(port, 'GET', '/api/campaigns/' + campaign)
            quiet.append((time.perf_counter() - started) * 1000)
        quiet.sort()
        print('== сцена в покое: медиана %.1f мс (%.1f..%.1f)' % (
            quiet[len(quiet) // 2], quiet[0], quiet[-1]), flush=True)

        for number in range(1, args.reps + 1):
            row = measure(port, campaign, version, 'rep%d' % number)
            rows.append(row)
            print('== ход %d: сам ход %s за %.2f с; сцена, запрошенная ЧЕРЕЗ него, '
                  'ответила за %.2f с (%s, версия %r)' % (
                      number, row['turn_status'], row['turn_seconds'] or 0,
                      row['scene_seconds'], row['scene_status'], row['scene_version']), flush=True)
    finally:
        stop_child(process, data / 'stop')

    waited = sorted(row['scene_seconds'] for row in rows)
    turns = sorted(row['turn_seconds'] for row in rows)
    result = dict(port=port, quiet_ms=quiet, rows=rows)
    (outdir / 'freeze.json').write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                        encoding='utf-8')
    print('\n== итог: ход медиана %.2f с; посторонний запрос во время хода медиана %.2f с '
          '(покой %.1f мс)' % (
              turns[len(turns) // 2], waited[len(waited) // 2], quiet[len(quiet) // 2]))
    print('== подробности:', outdir / 'freeze.json')
    bad = [row for row in rows if row['turn_status'] != 'resolved']
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
