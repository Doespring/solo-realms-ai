"""Recovery of item and activity charge pools through the real game cycle.

DELIVERY 14.203/14.205: a Long Rest does NOT imply dawn. The host emits
explicit sr/lr/dawn/day/dusk events, scores each once, and records the
result, seed and engine version in the same transaction as the charge change.
"""
import json
import uuid

import pytest

from solo.game_commands import GameCommand
from solo.inventory import Economy, new_item
from solo.nat20_api import Nat20GameEngine
from solo.nat20_recovery import event_id, seed_for
from solo.nat20_recovery_service import carried_slugs
from solo.storage import Store


@pytest.fixture
def campaign(tmp_path):
    store = Store(str(tmp_path / "db.sqlite3"))
    engine = Nat20GameEngine(store)
    record = engine.create_campaign("Recovery", seed=42)
    state = record["state"]
    economy = Economy.model_validate(state.get("economy") or {})
    for slug in ("wand-of-magic-missiles", "acid"):
        economy.items.append(new_item(slug, 1, "hero", "found in play"))
    state["economy"] = economy.model_dump()
    counters = state["nat20"]["hero"]["custom_counters"]
    counters["item_use:wand-of-magic-missiles"] = {"spent": 5}
    counters["item_use:acid"] = {"spent": 1}
    with store.connection(True) as db:
        db.execute("UPDATE campaigns SET state=? WHERE id=?", (json.dumps(state), record["id"]))
    return store, engine, record["id"]


def run(store, engine, cid, kind, **extra):
    record = store.campaign(cid)
    command = GameCommand.model_validate(dict(request_id=str(uuid.uuid4()),
        expected_version=record["version"], action=dict(kind=kind, **extra)))
    return engine.execute(cid, command)


def spent(store, cid, key):
    counters = store.campaign(cid)["state"]["nat20"]["hero"]["custom_counters"]
    return counters.get(key, {}).get("spent")


def events(store, cid):
    with store.connection() as db:
        return [dict(row) for row in db.execute(
            "SELECT id,event,game_minutes,seed,engine,result FROM recovery_events ORDER BY game_minutes")]


def test_carried_slugs_reads_the_campaign_state(campaign):
    """The regression that made recovery a no-op: state vs runtime."""
    store, engine, cid = campaign
    state = store.campaign(cid)["state"]
    assert "wand-of-magic-missiles" in carried_slugs(state)
    assert carried_slugs(state["nat20"]) == [], "runtime alone carries no items"


def test_long_rest_does_not_recharge_a_dawn_pool(campaign):
    store, engine, cid = campaign
    assert spent(store, cid, "item_use:wand-of-magic-missiles") == 5
    run(store, engine, cid, "long_rest", food_water=True)
    record = store.campaign(cid)
    assert record["state"]["elapsed_minutes"] == 480
    rows = events(store, cid)
    assert [r["event"] for r in rows] == ["dawn", "lr"]
    # Dawn fell inside the rest and recharged the wand; the long rest itself
    # contributed an empty delta.
    # The exact amount depends on 1d6+1 seeded from the campaign id, so assert
    # the direction and the recorded before-value, not one lucky roll.
    dawn = json.loads(rows[0]["result"])["delta"]
    assert dawn["item_use:wand-of-magic-missiles"]["before"] == 5
    assert dawn["item_use:wand-of-magic-missiles"]["after"] < 5
    assert json.loads(rows[1]["result"])["delta"] == {}
    # A consumable without a recovery rule is never refilled.
    assert spent(store, cid, "item_use:acid") == 1


def test_each_event_is_recorded_with_seed_and_engine(campaign):
    store, engine, cid = campaign
    run(store, engine, cid, "long_rest", food_water=True)
    for row in events(store, cid):
        assert row["engine"] == "dnd5e-engine 0.7.0+solo.2"
        assert row["id"] == event_id(cid, row["event"], row["game_minutes"])
        assert 0 <= row["seed"] < 2**63
        assert row["seed"] == seed_for(cid, row["id"], 42)


def test_redelivery_and_stale_events_are_refused(campaign):
    store, engine, cid = campaign
    run(store, engine, cid, "long_rest", food_water=True)
    runtime = {"seed": 42, "hero": {"custom_counters": {}}}
    state = store.campaign(cid)["state"]
    with store.connection(True) as db:
        with pytest.raises(ValueError, match="already applied"):
            Nat20GameEngine._recover(state, db, "x", cid, [("dawn", 360)], runtime)
    with store.connection(True) as db:
        with pytest.raises(ValueError, match="Stale recovery event"):
            Nat20GameEngine._recover(state, db, "x", cid, [("dusk", 300)], runtime)


def test_a_failed_command_leaves_no_recovery_row(tmp_path):
    store = Store(str(tmp_path / "db.sqlite3"))
    engine = Nat20GameEngine(store)
    record = engine.create_campaign("Atomic", seed=42)
    state = record["state"]
    state["nat20"]["hero"]["custom_counters"]["item_use:wand-of-magic-missiles"] = {"spent": 5}
    with store.connection(True) as db:
        db.execute("UPDATE campaigns SET state=? WHERE id=?", (json.dumps(state), record["id"]))
    original = Nat20GameEngine._recover
    def boom(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash after the calculation")
    Nat20GameEngine._recover = staticmethod(boom)
    try:
        with pytest.raises(RuntimeError):
            run(store, engine, record["id"], "long_rest", food_water=True)
    finally:
        Nat20GameEngine._recover = staticmethod(original)
    current = store.campaign(record["id"])
    assert current["version"] == record["version"]
    assert current["state"].get("elapsed_minutes") is None
    assert events(store, record["id"]) == []
