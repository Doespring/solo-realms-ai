"""Одноразовая уборка осиротевших строк индекса памяти в живом профиле.

Зачем: в `runtime/web-local/solo.sqlite3` таблица `memory_records` пуста
(0 строк), а `memory_fts` держит 8 строк с rowid 1..8, `memory_subjects` — 12,
`memory_access` — 11. Строки остались от удалённых кампаний: `memory_records`
уходит каскадом вместе с `events`, а FTS-индекс и связи никто не чистит
(`solo/memory.py` пишет в `memory_fts` только INSERT, ни одного удаления).

Последствие: `Memory.put()` вставляет новый `memory_records` с rowid 1
(таблица пуста), затем `INSERT INTO memory_fts(rowid,...)` на уже занятый
rowid 1 → `sqlite3.IntegrityError: constraint failed` → 500 на
`POST /api/nat20/campaigns` (создание кампании вообще, не только тестовой).

Что делает скрипт: удаляет из `memory_fts`, `memory_subjects`, `memory_access`
только те строки, чей rowid отсутствует в `memory_records`. Ни одна живая
запись памяти не затрагивается: `memory_records` = 0, значит осиротевших нет.
Скрипт печатает счётчики до и после и отказывается работать без --apply.

Запуск:
    python3 r192_fts_orphans.py --db runtime/web-local/solo.sqlite3           # только показать
    python3 r192_fts_orphans.py --db runtime/web-local/solo.sqlite3 --apply   # починить
"""
import argparse
import sqlite3

ORPHAN_TABLES = ('memory_fts', 'memory_subjects', 'memory_access')
LIVE = 'memory_records'


def counts(db):
    out = {name: db.execute('SELECT count(*) FROM ' + name).fetchone()[0]
           for name in (LIVE,) + ORPHAN_TABLES}
    out['orphans_fts'] = db.execute(
        'SELECT count(*) FROM memory_fts WHERE rowid NOT IN (SELECT id FROM memory_records)'
    ).fetchone()[0]
    out['orphans_subjects'] = db.execute(
        'SELECT count(*) FROM memory_subjects WHERE rowid NOT IN (SELECT id FROM memory_records)'
    ).fetchone()[0]
    out['orphans_access'] = db.execute(
        'SELECT count(*) FROM memory_access WHERE rowid NOT IN (SELECT id FROM memory_records)'
    ).fetchone()[0]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    db = sqlite3.connect(args.db, timeout=30)
    db.execute('PRAGMA foreign_keys=ON')
    before = counts(db)
    print('до:', before)
    if not args.apply:
        print('режим показа: изменения не вносились (нужен --apply)')
        return 0
    if before['orphans_fts'] == 0 and before['orphans_subjects'] == 0 and before['orphans_access'] == 0:
        print('осиротевших строк нет, чинить нечего')
        return 0
    with db:
        for name in ORPHAN_TABLES:
            db.execute('DELETE FROM ' + name + ' WHERE rowid NOT IN (SELECT id FROM memory_records)')
    after = counts(db)
    print('после:', after)
    db.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
    db.commit()
    print('после rebuild:', counts(db))
    ok = all(after[key] == 0 for key in ('orphans_fts', 'orphans_subjects', 'orphans_access'))
    print('ИТОГ:', 'индекс чист' if ok else 'остались сироты — читать счётчики')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
