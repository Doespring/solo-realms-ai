import json
import uuid

import pytest

from solo.game_commands import GameCommand
from solo.inventory import Economy, new_item
from solo.nat20_api import Nat20GameEngine
from solo.nat20_recovery import check_order
from solo.storage import Store
from solo.world import ensure
from solo.world_service import WorldCommand, WorldService

WAND = "item_use:wand-of-magic-missiles"


def build(tmp_path, name, start, seed=42):
    store = Store(str(tmp_path / (name + '.sqlite3')))
    engine = Nat20GameEngine(store)
    record = engine.create_campaign(name, seed=seed)
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


def spent(store, cid):
    counters = store.campaign(cid)["state"]["nat20"]["hero"]["custom_counters"]
    return counters.get(WAND, {}).get("spent")


def events(store):
    with store.connection() as db:
        return [(r["event"], r["game_minutes"]) for r in db.execute(
            "SELECT event,game_minutes FROM recovery_events ORDER BY game_minutes,event")]


def test_check_order_allows_two_events_in_one_minute():
    """A rest ending exactly at dawn and the dawn are both real."""
    cursor = {"minutes": 360, "event_id": "recover:c:dawn:360"}
    check_order(cursor, "sr", 360, "recover:c:sr:360")
    with pytest.raises(ValueError, match="already applied"):
        check_order(cursor, "dawn", 360, "recover:c:dawn:360")
    with pytest.raises(ValueError, match="Stale"):
        check_order(cursor, "lr", 359, "recover:c:lr:359")


def test_short_rest_ending_at_dawn_keeps_both_events(tmp_path):
    store, engine, cid = build(tmp_path, "sr300", 300)
    act(store, engine, cid, "short_rest", hit_dice=0)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 360
    assert events(store) == [("dawn", 360), ("sr", 360)]
    # Dawn recharged the wand (1d6+1 from the campaign seed); the short rest
    # added nothing to its own pool, and the clock was NOT nudged to dodge it.
    assert spent(store, cid) < 5


def test_long_rest_ending_at_a_new_day_keeps_both_events(tmp_path):
    store, engine, cid = build(tmp_path, "lr960", 960)
    act(store, engine, cid, "long_rest", food_water=True)
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 1440
    # build() set the clock to 960 behind the recovery paths, so this rest
    # catches up on the skipped dawn as well as the day it spans.
    assert events(store) == [("dawn", 360), ("dusk", 1080), ("day", 1440), ("lr", 1440)]
    # The caught-up dawn recharged the wand; the day and the long rest added
    # nothing, since the wand recovers at dawn only.
    assert spent(store, cid) < 5


def test_travel_across_dawn_recharges_in_the_same_transaction(tmp_path):
    store, engine, cid = build(tmp_path, "travel", 359)
    state = store.campaign(cid)["state"]
    destination = "ProbeBridgeEnd"
    state["locations"].append({"name": destination, "description": "bridge", "npc": [],
                              "objects": [], "facts": [], "known_to_hero": True})
    from solo.travel import geography
    ensure(state)
    graph = geography(state)
    start = next((n for n in graph["nodes"] if n["name"] == state["location"]), None)
    if start is None:
        start = {"id": "ProbeStart", "name": state["location"], "coordinates": [1, 1]}
        graph["nodes"].append(start)
    graph["nodes"].append({"id": "ProbeEnd", "name": destination,
                           "coordinates": [5, 5], "known": True})
    graph["routes"].append({"from": start["id"], "to": "ProbeEnd", "meters": 120,
                            "minutes": 2, "known": True, "description": "bridge"})
    state["geography"] = graph
    with store.connection(True) as db:
        db.execute("UPDATE campaigns SET state=? WHERE id=?", (json.dumps(state), cid))
    record = store.campaign(cid)
    command = WorldCommand.model_validate(dict(request_id=str(uuid.uuid4()),
        expected_version=record["version"], kind="travel", target=destination))
    WorldService(store).execute(cid, command)
    current = store.campaign(cid)["state"]
    assert current["elapsed_minutes"] == 361
    assert current["location"] == destination
    assert events(store) == [("dawn", 360)]
    # Walking through dawn recharged the wand: below the spent=5 it started at.
    assert spent(store, cid) < 5


def test_a_redelivery_after_another_event_and_after_restart_is_refused(tmp_path):
    store, engine, cid = build(tmp_path, "dup", 300)
    act(store, engine, cid, "short_rest", hit_dice=0)
    act(store, engine, cid, "short_rest", hit_dice=0)
    reopened = Store(str(tmp_path / "dup.sqlite3"))
    runtime = {"seed": 42, "hero": {"custom_counters": {}}}
    state = reopened.campaign(cid)["state"]
    with reopened.connection(True) as db:
        with pytest.raises(ValueError, match="already applied"):
            Nat20GameEngine._recover(state, db, "x", cid, [("dawn", 360)], runtime)
    with reopened.connection(True) as db:
        with pytest.raises(ValueError, match="Stale recovery event"):
            Nat20GameEngine._recover(state, db, "x", cid, [("dusk", 300)], runtime)


def test_a_boundary_crossed_outside_the_recovery_paths_is_caught_up(tmp_path):
    """A scenario wait moves the clock; the next command must still score dawn."""
    store, engine, cid = build(tmp_path, "catchup", 300)
    state = store.campaign(cid)["state"]
    destination = "ProbeBridgeEnd"
    state["locations"].append({"name": destination, "description": "bridge",
                              "npc": [], "objects": [], "facts": [],
                              "known_to_hero": True})
    from solo.travel import geography
    from solo.world import ensure as _ensure
    _ensure(state)
    graph = geography(state)
    start = next((n for n in graph["nodes"] if n["name"] == state["location"]), None)
    if start is None:
        start = {"id": "ProbeStart", "name": state["location"], "coordinates": [1, 1]}
        graph["nodes"].append(start)
    graph["nodes"].append({"id": "ProbeEnd", "name": destination,
                           "coordinates": [5, 5], "known": True})
    graph["routes"].append({"from": start["id"], "to": "ProbeEnd", "meters": 120,
                            "minutes": 2, "known": True, "description": "bridge"})
    state["geography"] = graph
    # An outside writer (a scenario wait) moves the clock past dawn without
    # ever consulting the recovery module.
    state["elapsed_minutes"] = 360
    with store.connection(True) as db:
        db.execute("UPDATE campaigns SET state=? WHERE id=?", (json.dumps(state), cid))
    assert events(store) == []
    record = store.campaign(cid)
    command = WorldCommand.model_validate(dict(request_id=str(uuid.uuid4()),
        expected_version=record["version"], kind="travel", target=destination))
    WorldService(store).execute(cid, command)
    # The next mutating command catches up from the scored frontier.
    assert events(store) == [("dawn", 360)]
    assert spent(store, cid) < 5


def test_a_nat20_campaign_refuses_the_legacy_rest_command(tmp_path):
    """The old resting path must not move the clock behind the engine."""
    from fastapi.testclient import TestClient
    from solo.app import create_app
    store, engine, cid = build(tmp_path, "restr", 960)
    client = TestClient(create_app(str(tmp_path / "restr.sqlite3")))
    record = store.campaign(cid)
    answer = client.post("/api/campaigns/%s/screen/commands" % cid,
                         json=dict(request_id=str(uuid.uuid4()), kind="rest",
                                   expected_version=record["version"], parameters={}))
    assert answer.status_code == 409
    assert store.campaign(cid)["state"]["elapsed_minutes"] == 960
    assert events(store) == []
