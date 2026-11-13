"""n00: что `world-readiness` отвечает на настоящем профиле r239 в трёх состояниях.

Профиль — копия живого (книга 323 страницы уже разобрана), модель офлайн-мок:
ни одного платного вызова. Состояния переключаются прямо в базе копии, чтобы
дождаться ровно той строки, что оставил прогон r239.

    .venv/bin/python scripts/probe_n00_state.py --data-dir runtime/n00-live-2023
"""
import argparse, json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--draft', default='85f962a6-ed81-4f3a-be01-e22226c7e8b1',
                        help='черновик r239 по умолчанию; иначе самый свежий без кампании')
    parser.add_argument('--out', default='')
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    from solo.app import create_app
    from solo.llm import MockLLM

    data = Path(args.data_dir).resolve()
    db_path = data / 'preparation.sqlite3'
    if not db_path.is_file():
        raise SystemExit('Нет профиля подготовки в ' + str(data))

    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    draft = args.draft
    exists = raw.execute('SELECT id FROM drafts WHERE id=?', (draft,)).fetchone()
    if exists is None:
        draft = raw.execute("SELECT id FROM drafts WHERE campaign IS NULL ORDER BY updated DESC LIMIT 1").fetchone()['id']
    run_id = raw.execute('SELECT id,status,done,total,playable FROM creation_runs WHERE draft=? ORDER BY created DESC LIMIT 1',
                         (draft,)).fetchone()['id']

    def force(status, playable):
        row = raw.execute('SELECT done,total FROM creation_runs WHERE id=?', (run_id,)).fetchone()
        total = max(int(row['done']), int(row['total']))
        raw.execute('UPDATE creation_runs SET status=?,done=?,total=?,playable=? WHERE id=?',
                    (status, total, total, playable, run_id))
        raw.commit()

    report = dict(draft=draft, run=run_id, states=[])
    app = create_app(data / 'solo.sqlite3', MockLLM(), experimental=True, offline_mock=True)
    with TestClient(app) as client:
        for status, playable, label in (('ready', 1, 'как лежит в профиле'),
                                        ('partial', 1, 'r239: partial, глава собрана'),
                                        ('partial', 0, 'partial без отметки «можно играть»')):
            force(status, playable)
            response = client.get('/api/creation/drafts/' + draft + '/world-readiness')
            body = response.json()
            report['states'].append(dict(state=label, status=status, playable=playable,
                                         http=response.status_code, body=body))
            print('\n==', label, '| status=' + status, 'playable=' + str(playable),
                  '| HTTP', response.status_code)
            print(json.dumps(body, ensure_ascii=False, indent=2))
    force('ready', 1)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
