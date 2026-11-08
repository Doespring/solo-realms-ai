"""Crossed boundaries commit with their action; a reload does not block play.

DELIVERY after round 268: the owner reproduced two defects in the recovery
integration. A wait through ordinary chat left the charges for the next
command, and a save/load made the abandoned future refuse a fresh rest.
"""
import json
import uuid

import pytest

from solo.contracts import Plan
from solo.game_commands import GameCommand
from solo.inventory import Economy, new_item
from solo.nat20_api import Nat20GameEngine
from solo.storage import Store

WAND = "item_use:wand-of-magic-missiles"


def build(tmp_path, name, start=300):
    store = Store(str(tmp_path / (name + '.sqlite3')))
    engine = Nat20GameEngine(store)
    record = engine.create_campaign(name, seed=42)
    state = record["state"]
    economy = Economy.model_validate(state.get("economy") or {})
    economy.items.append(new_item("wand-of-magic-missiles", 1, "hero", "found"))
    state["economy"] = economy.model_dump()
    state["nat20"]["hero"]["custom_counters"][WAND] = {"spent": 5}
    state["elapsed_minutes"] = start
    with store.connection(True) as db:
        db.execute("UPDATE campaigns SET state=? WHERE id=?", (json.dumps(state), record["id"]))
    return store, engine, record["id"]


def act(store, engine, cid, kind, **extra):
    record = store.campaign(cid)
    command = GameCommand.model_validate(dict(request_id=str(uuid.uuid4()),
        expected_version=record["version"], action=dict(kind=kind, **extra)))
    return engine.execute(cid, command)


def wait(store, cid, minutes=65):
    """A wait through ordinary chat: a plan resolved by Store.apply."""
    record = store.campaign(cid)
    plan = Plan(summary="жду", steps=[dict(kind="wait", text="подожду")])
    with store.connection(True) as db:
        db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?)",
                   (str(uuid.uuid4()), cid, str(uuid.uuid4()), "жду",
                    plan.model_dump_json(), "pending", record["version"],
                    json.dumps({"route": "scenario", "natural": True}), 0.0))
        action = db.execute("SELECT id FROM actions WHERE campaign=? ORDER BY rowid DESC"
                            " LIMIT 1", (cid,)).fetchone()[0]
        state = record["state"]
        state["elapsed_minutes"] = int(state["elapsed_minutes"]) + minutes
        db.execute("UPDATE campaigns SET state=? WHERE id=?", (json.dumps(state), cid))
    return store.apply(action)


def spent(store, cid):
    counters = store.campaign(cid)["state"]["nat20"]["hero"]["custom_counters"]
    return counters.get(WAND, {}).get("spent")


def events(store):
    with store.connection() as db:
        return [(row["event"], row["game_minutes"]) for row in db.execute(
            "SELECT event,game_minutes FROM recovery_events ORDER BY game_minutes,rowid")]


def test_a_chat_wait_restores_at_once_without_a_later_rest(tmp_path):
    store, engine, cid = build(tmp_path, "wait")
    assert spent(store, cid) == 5
    wait(store, cid, 65)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 365
    assert ("dawn", 360) in events(store)
    assert spent(store, cid) < 5


def test_a_reload_does_not_block_a_fresh_rest_on_the_same_minute(tmp_path):
    """Owner reproduction: save at minute 300, rest to 360, load, rest again."""
    store, engine, cid = build(tmp_path, "restore")
    checkpoint = store.save(cid, "перед загрузкой")
    act(store, engine, cid, "short_rest", hit_dice=0)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 360
    store.restore(cid, checkpoint)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 300
    act(store, engine, cid, "short_rest", hit_dice=0)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 360
    # History is kept: the abandoned row is still there, owned by the new line.
    assert ("dawn", 360) in events(store)


def test_an_undo_does_not_block_a_fresh_rest_on_the_same_minute(tmp_path):
    store, engine, cid = build(tmp_path, "undo")
    act(store, engine, cid, "short_rest", hit_dice=0)
    store.undo(cid)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 300
    act(store, engine, cid, "short_rest", hit_dice=0)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 360


def test_a_redelivery_inside_the_same_line_is_still_refused(tmp_path):
    store, engine, cid = build(tmp_path, "same")
    act(store, engine, cid, "short_rest", hit_dice=0)
    runtime = store.campaign(cid)["state"]["nat20"]
    with store.connection(True) as db:
        with pytest.raises(ValueError, match="already applied"):
            Nat20GameEngine._recover(store.campaign(cid)["state"], db, cid, cid,
                                     [("dawn", 360)], runtime, started=300)


def test_a_second_rest_does_not_replay_the_scored_boundary(tmp_path):
    store, engine, cid = build(tmp_path, "twice")
    act(store, engine, cid, "short_rest", hit_dice=0)
    act(store, engine, cid, "short_rest", hit_dice=0)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 420
    assert events(store).count(("dawn", 360)) == 1


def test_a_crash_between_the_clock_and_the_commit_rolls_back_everything(tmp_path):
    store, engine, cid = build(tmp_path, "crash")
    before = store.campaign(cid)["state"]
    runtime = before["nat20"]
    def bomb(*args, **kwargs):
        raise RuntimeError("crash between calculation and commit")
    with store.connection(True) as db:
        state = json.loads(json.dumps(before))
        state["elapsed_minutes"] = 400
        original = Nat20GameEngine._recover
        Nat20GameEngine._recover = staticmethod(bomb)
        try:
            with pytest.raises(RuntimeError):
                Nat20GameEngine._recover(state, db, cid, cid, [("dawn", 360)], runtime,
                                         started=300)
        finally:
            Nat20GameEngine._recover = staticmethod(original)
    after = store.campaign(cid)["state"]
    assert after["elapsed_minutes"] == before["elapsed_minutes"]
    assert spent(store, cid) == 5
    assert events(store) == []

def test_a_save_older_than_the_played_line_still_owes_its_charges(tmp_path):
    """A load must not leave a cursor ahead of the clock and swallow dawn."""
    store, engine, cid = build(tmp_path, "old")
    checkpoint = store.save(cid, "до отдыха")
    act(store, engine, cid, "short_rest", hit_dice=0)
    assert store.campaign(cid)["state"]["nat20"]["recovery_minute"] == 360
    store.restore(cid, checkpoint)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 300
    # The reloaded cursor must not sit ahead of the clock.
    from solo.nat20_recovery_service import cursor_of
    assert cursor_of(store.campaign(cid)["state"]) <= 300
    act(store, engine, cid, "short_rest", hit_dice=0)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 360
    assert ("dawn", 360) in events(store)