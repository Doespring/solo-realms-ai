"""Хранилище сайта: аккаунты, герои, кампании, лента. Один SQLite-файл.

Никаких ORM и внешних зависимостей: сайт должен запускаться там, где есть
только Python из .venv проекта.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get("SOLO_SITE_DB", Path(__file__).with_name("data") / "site.db"))

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    email     TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL,
    pw_hash   TEXT NOT NULL,
    created   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token     TEXT PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS heroes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    species    TEXT NOT NULL,
    klass      TEXT NOT NULL,
    background TEXT NOT NULL,
    level      INTEGER NOT NULL DEFAULT 1,
    abilities  TEXT NOT NULL,
    sheet      TEXT NOT NULL,
    created    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hero_id   INTEGER NOT NULL REFERENCES heroes(id) ON DELETE CASCADE,
    title     TEXT NOT NULL,
    scene     TEXT NOT NULL,
    turn      INTEGER NOT NULL DEFAULT 0,
    status    TEXT NOT NULL DEFAULT 'active',
    created   INTEGER NOT NULL,
    updated   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',
    created     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_campaign ON messages(campaign_id, id);
CREATE INDEX IF NOT EXISTS idx_heroes_user ON heroes(user_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_user ON campaigns(user_id);
"""


def now() -> int:
    return int(time.time())


class Store:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ── аккаунты ───────────────────────────────────────────────────────────
    def create_user(self, email: str, name: str, pw_hash: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO users(email, name, pw_hash, created) VALUES(?,?,?,?)",
            (email.strip().lower(), name.strip(), pw_hash, now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def user_by_email(self, email: str):
        return self._conn.execute(
            "SELECT * FROM users WHERE email=?", (email.strip().lower(),)
        ).fetchone()

    def user_by_id(self, uid: int):
        return self._conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    # ── сессии ─────────────────────────────────────────────────────────────
    def open_session(self, token: str, user_id: int) -> None:
        self._conn.execute(
            "INSERT INTO sessions(token, user_id, created) VALUES(?,?,?)",
            (token, user_id, now()),
        )
        self._conn.commit()

    def user_by_token(self, token: str):
        return self._conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (token,),
        ).fetchone()

    def close_session(self, token: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        self._conn.commit()

    # ── герои ──────────────────────────────────────────────────────────────
    def add_hero(self, user_id: int, name: str, species: str, klass: str,
                 background: str, level: int, abilities: dict, sheet: dict) -> int:
        cur = self._conn.execute(
            """INSERT INTO heroes(user_id,name,species,klass,background,level,abilities,sheet,created)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (user_id, name, species, klass, background, level,
             json.dumps(abilities), json.dumps(sheet), now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def heroes(self, user_id: int):
        return self._conn.execute(
            "SELECT * FROM heroes WHERE user_id=? ORDER BY id DESC", (user_id,)
        ).fetchall()

    def hero(self, hero_id: int):
        return self._conn.execute("SELECT * FROM heroes WHERE id=?", (hero_id,)).fetchone()

    def save_sheet(self, hero_id: int, sheet: dict) -> None:
        self._conn.execute("UPDATE heroes SET sheet=? WHERE id=?",
                           (json.dumps(sheet), hero_id))
        self._conn.commit()

    def delete_hero(self, hero_id: int) -> None:
        self._conn.execute("DELETE FROM heroes WHERE id=?", (hero_id,))
        self._conn.commit()

    # ── кампании ───────────────────────────────────────────────────────────
    def add_campaign(self, user_id: int, hero_id: int, title: str, scene: str) -> int:
        ts = now()
        cur = self._conn.execute(
            """INSERT INTO campaigns(user_id,hero_id,title,scene,turn,status,created,updated)
               VALUES(?,?,?,?,0,'active',?,?)""",
            (user_id, hero_id, title, scene, ts, ts),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def campaigns(self, user_id: int):
        return self._conn.execute(
            """SELECT c.*, h.name AS hero_name, h.klass AS hero_class
               FROM campaigns c JOIN heroes h ON h.id=c.hero_id
               WHERE c.user_id=? ORDER BY c.updated DESC""",
            (user_id,),
        ).fetchall()

    def campaign(self, cid: int):
        return self._conn.execute(
            """SELECT c.*, h.name AS hero_name, h.klass AS hero_class, h.level AS hero_level,
                      h.sheet AS hero_sheet, h.abilities AS hero_abilities
               FROM campaigns c JOIN heroes h ON h.id=c.hero_id WHERE c.id=?""",
            (cid,),
        ).fetchone()

    def touch_campaign(self, cid: int, scene: str, turn: int) -> None:
        self._conn.execute(
            "UPDATE campaigns SET scene=?, turn=?, updated=? WHERE id=?",
            (scene, turn, now(), cid),
        )
        self._conn.commit()

    def delete_campaign(self, cid: int) -> None:
        self._conn.execute("DELETE FROM campaigns WHERE id=?", (cid,))
        self._conn.commit()

    # ── лента ──────────────────────────────────────────────────────────────
    def add_message(self, campaign_id: int, role: str, text: str, meta: dict | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO messages(campaign_id,role,text,meta,created) VALUES(?,?,?,?,?)",
            (campaign_id, role, text, json.dumps(meta or {}, ensure_ascii=False), now()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def messages(self, campaign_id: int, limit: int = 200):
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE campaign_id=? ORDER BY id DESC LIMIT ?",
            (campaign_id, limit),
        ).fetchall()
        return list(reversed(rows))
