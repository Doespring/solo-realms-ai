"""An actor hidden from the table must not be named as a subject of a memory."""
import json
import os
os.environ['SOLO_TEST']='1'

from solo.contracts import Hero
from solo.memory import Memory
from solo.rulesets import BUILTINS
from solo.storage import Store


def _snapshot(actor):
    return json.dumps({"actors": [actor]}, ensure_ascii=False)


def test_a_hidden_actor_is_not_named_to_the_hero(tmp_path):
    store = Store(tmp_path / "hidden.sqlite3")
    memory = Memory(store)
    cid = store.create("Тайна", Hero(name="Герой"), BUILTINS["dnd2024"])["id"]
    store.record_scenario(cid, "walk-1", "Иду",
                          lambda state: [dict(type="scripted", text="Пусто.")])
    event = store.history(cid)[-1]["id"]
    with store.connection(True) as db:
        campaign_marker = cid
        hero = memory.entity(db, cid, "hero", "Герой")
        subject = memory.entity(db, cid, "npc", "Убийца")
        record = memory.put(db, campaign=cid, event=event, key="k-hidden",
                            kind="fact", provenance="scenario", truth="fact",
                            scope="public", location="", importance=9,
                            minutes=0, text="Кто-то стоял в тени",
                            recipients=(hero,), subjects=(subject,))
        db.execute("INSERT INTO memory_engine_events VALUES(?,?,?,?,?,?,?)",
                   (event, campaign_marker, "engine-1", "br-1", 1, None, "sig-1"))
        db.execute("UPDATE events SET results=? WHERE id=?",
                   (json.dumps([{"type": "attack", "game_snapshot":
                                 json.loads(_snapshot({"kind": "npc", "id": "Убийца",
                                                    "scope": "secret",
                                                    "known_by": []}))}]),
                    event))
    found = memory.retrieve(cid, audience=[hero], limit=10)["records"]
    named = [r for r in found if r["id"] == record]
    assert named, "the record itself disappeared"
    assert subject not in named[0]["subjects"], (
        "a secret actor was named to the hero: %r" % named[0]["subjects"])

def test_an_actor_known_only_by_others_is_not_named(tmp_path):
    store = Store(tmp_path / "knownby.sqlite3")
    memory = Memory(store)
    cid = store.create("Круг", Hero(name="Герой"), BUILTINS["dnd2024"])["id"]
    store.record_scenario(cid, "walk-1", "Иду",
                          lambda state: [dict(type="scripted", text="Пусто.")])
    event = store.history(cid)[-1]["id"]
    with store.connection(True) as db:
        hero = memory.entity(db, cid, "hero", "Герой")
        subject = memory.entity(db, cid, "npc", "Наёмник")
        record = memory.put(db, campaign=cid, event=event, key="k-kb",
                            kind="fact", provenance="scenario", truth="fact",
                            scope="public", location="", importance=9,
                            minutes=0, text="Кто-то ждал у ворот",
                            recipients=(hero,), subjects=(subject,))
        db.execute("INSERT INTO memory_engine_events VALUES(?,?,?,?,?,?,?)",
                   (event, cid, "engine-2", "br-2", 1, None, "sig-2"))
        db.execute("UPDATE events SET results=? WHERE id=?",
                   (json.dumps([{"type": "attack", "game_snapshot": {
                       "actors": [{"kind": "npc", "id": "Наёмник",
                                   "scope": "public",
                                   "known_by": ["чужой-зритель"]}]}}],
                       ensure_ascii=False), event))
    found = memory.retrieve(cid, audience=[hero], limit=10)["records"]
    named = [r for r in found if r["id"] == record]
    assert named, "the record itself disappeared"
    assert subject not in named[0]["subjects"], (
        "an actor known only by others was named: %r" % named[0]["subjects"])
