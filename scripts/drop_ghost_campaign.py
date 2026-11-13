"""Убрать из живого профиля одну строку кампании, созданную сорвавшимся запросом.

Контекст: до починки индекса памяти `POST /api/nat20/campaigns` отвечал 500
после того, как строка кампании уже была вставлена. Осталась строка
4a76baa4-5a46-4f01-8f29-4cf38d0210cb («Проверка стенда r192», version=0) без
событий и записей памяти — то есть пустая кампания-призрак с тем же именем,
что и рабочая. Скрипт удаляет ровно эту строку по id и отказывается работать,
если у неё вдруг появились события или память.
"""
import sqlite3
import sys

GHOST = '4a76baa4-5a46-4f01-8f29-4cf38d0210cb'
DB = sys.argv[1] if len(sys.argv) > 1 else 'runtime/web-local/solo.sqlite3'

db = sqlite3.connect(DB, timeout=30)
row = db.execute('SELECT id,name,version FROM campaigns WHERE id=?', (GHOST,)).fetchone()
if not row:
    print('строки нет — убирать нечего')
    raise SystemExit(0)
print('строка:', row)
events = db.execute('SELECT count(*) FROM events WHERE campaign=?', (GHOST,)).fetchone()[0]
memory = db.execute('SELECT count(*) FROM memory_records WHERE campaign=?', (GHOST,)).fetchone()[0]
print('событий:', events, '| записей памяти:', memory)
if events or memory:
    raise SystemExit('у кампании есть история — удаление отменено, разбирать вручную')
with db:
    db.execute('DELETE FROM campaigns WHERE id=?', (GHOST,))
print('кампаний после:', db.execute('SELECT count(*) FROM campaigns').fetchone()[0])
for r in db.execute('SELECT id,name,version FROM campaigns'):
    print('  ', r)
