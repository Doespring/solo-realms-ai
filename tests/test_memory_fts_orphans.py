"""r193: память переживает осиротевшие rowid FTS-индекса после удаления кампании.

`memory_records.event_id` уходит каскадом вместе с `events`, а `memory_fts`
внешних ключей не знает, поэтому строка индекса остаётся занятой. Следующая
кампания получает тот же id записи и падала `IntegrityError: constraint failed`
на `INSERT INTO memory_fts(rowid,...)` — 500 на создании кампании.
"""
import sqlite3

from solo.contracts import Hero
from solo.storage import Store


def remember(store, campaign, key, text):
    """Одно scripted-событие, чьи структурированные свидетельства пишут память."""
    def transition(state):
        return [dict(type='scripted', memory_evidence=[dict(
            text=text, provenance='scenario', truth='fact', scope='private',
            known_by=['hero'], subjects=['hero'], importance=8,
            source_ref='r193:' + key)])]
    return store.record_scenario(campaign, key, 'Запись памяти', transition)


def hero_id(store, campaign):
    return store.memory.identities(campaign, store.campaign(campaign)['state'])['hero']


def drop_campaign(store, campaign):
    """Удаление кампании целиком — как это делает обслуживание живого профиля.

    В приложении маршрута удаления нет, поэтому порядок задан вручную: без него
    внешние ключи на `campaigns` не дают убрать строку кампании.
    """
    with store.connection(True) as db:
        db.execute('DELETE FROM memory_engine_events WHERE campaign=?', (campaign,))
        db.execute('DELETE FROM memory_records WHERE campaign=?', (campaign,))
        db.execute('DELETE FROM memory_entities WHERE campaign=?', (campaign,))
        db.execute('DELETE FROM events WHERE campaign=?', (campaign,))
        db.execute('DELETE FROM actions WHERE campaign=?', (campaign,))
        db.execute('DELETE FROM campaigns WHERE id=?', (campaign,))


def fts_orphans(store):
    with store.connection() as db:
        return db.execute('SELECT count(*) FROM memory_fts WHERE rowid NOT IN '
                          '(SELECT id FROM memory_records)').fetchone()[0]


def found(store, campaign, query):
    return [row['content'] for row in
            store.memory.retrieve(campaign, audience=[hero_id(store, campaign)], query=query)['records']]


def indexed(store, query):
    with store.connection() as db:
        return db.execute('SELECT count(*) FROM memory_fts WHERE memory_fts MATCH ?',
                          ('"' + query + '"*',)).fetchone()[0]


def test_memory_survives_fts_rowid_of_deleted_campaign(tmp_path):
    """Удалить кампанию → создать новую → записать память: без исключения."""
    store = Store(tmp_path / 'memory.sqlite3')
    first = store.create('Сгоревшая кампания', Hero(name='Мэй', class_name='wizard'))
    remember(store, first['id'], 'r193-first', 'Старый маяк зовётся АЛЬТАИР')
    assert any('АЛЬТАИР' in text for text in found(store, first['id'], 'маяк'))

    drop_campaign(store, first['id'])
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM memory_records').fetchone()[0] == 0

    second = store.create('Новая кампания', Hero(name='Мэй', class_name='wizard'))
    # Здесь старая сборка падала sqlite3.IntegrityError: constraint failed —
    # новая запись получала rowid 1, ещё занятый индексом сгоревшей кампании.
    remember(store, second['id'], 'r193-second', 'Новый трактир зовётся БЕТА')

    texts = found(store, second['id'], 'трактир')
    assert any('БЕТА' in text for text in texts), texts
    assert not any('АЛЬТАИР' in text for text in texts), texts
    assert indexed(store, 'трактир') == 1
    assert fts_orphans(store) == 0, 'индекс обязан уходить вместе с записью'
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM memory_records WHERE campaign=?',
                          (first['id'],)).fetchone()[0] == 0


def test_memory_heals_profile_that_already_carries_orphaned_index_rows(tmp_path):
    """База старой сборки: триггера нет, запись удалена, FTS-строка осталась."""
    path = tmp_path / 'legacy.sqlite3'
    store = Store(path)
    first = store.create('Старая кампания', Hero(name='Мэй', class_name='wizard'))
    remember(store, first['id'], 'r193-legacy', 'Старый маяк зовётся АЛЬТАИР')

    raw = sqlite3.connect(path)
    try:
        raw.execute('DROP TRIGGER memory_fts_drop_with_record')
    except sqlite3.OperationalError:
        pass  # сборка без триггера — ровно то состояние, которое чиним
    raw.execute('PRAGMA foreign_keys=OFF')
    raw.execute('DELETE FROM memory_records')
    raw.commit()
    raw.close()
    assert fts_orphans(store) == 1, 'условие дефекта: строка индекса без записи'

    second = store.create('Новая кампания', Hero(name='Мэй', class_name='wizard'))
    remember(store, second['id'], 'r193-fresh', 'Новый трактир зовётся БЕТА')

    assert any('БЕТА' in text for text in found(store, second['id'], 'трактир'))
    assert fts_orphans(store) == 0
