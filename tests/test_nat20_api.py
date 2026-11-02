"""Targeted optional-backend acceptance tests; fresh SQLite only, no UI or LLM."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from solo.contracts import Hero
from solo.nat20_api import GameCommand, Nat20GameEngine
from solo.nat20_content import CONTENT, ROOT, release
from solo.rulesets import BUILTINS
from solo.storage import Store


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network forbidden in Nat20 acceptance tests")
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def game(tmp_path):
    store = Store(tmp_path / "nat20-test.sqlite3")
    api = Nat20GameEngine(store)
    campaign = api.create_campaign("Nat20 fixture", seed=5, hp_current=7, second_wind_spent=2)["id"]
    return api, campaign


def command(api, cid, id, **action):
    return GameCommand(request_id=id, expected_version=api.store.campaign(cid)["version"], action=action)


def run(api, cid, id, **action):
    return api.execute(cid, command(api, cid, id, **action))


def fresh_process_restore(database, campaign):
    # This is fixed trusted harness code, not model/PDF text or game content.
    program = """
import json,sys
from solo.storage import Store
from solo.nat20_api import Nat20GameEngine
r=Nat20GameEngine(Store(sys.argv[1])).restore(sys.argv[2])
print(json.dumps({'runtime':r['runtime'],'snapshot':r['snapshot'].model_dump(mode='json'),'version':r['version']},sort_keys=True))
"""
    process = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", program, str(database), campaign],
        cwd=ROOT, text=True, encoding="utf-8", capture_output=True, check=True, timeout=45,
        env=dict(os.environ, SOLO_TEST="1", PYTHONDONTWRITEBYTECODE="1"))
    return json.loads(process.stdout)


def test_one_check_and_both_rests_use_native_resolvers_and_solo_journal(game):
    api, cid = game
    body = command(api, cid, "check", kind="check", check_id="athletics")
    result = api.execute(cid, body)
    assert result["result"]["modifier"] == 5
    assert result["result"]["roll_total"] == result["result"]["natural_roll"] + 5
    assert result["result"]["success"] == (result["result"]["roll_total"] >= 15)
    assert result["event"].contract_version == result["snapshot"].contract_version == "memory-v1"
    assert result["event"].payload["effects"][0]["type"] == "roll"
    assert api.execute(cid, body)["journal"] == result["journal"]
    with pytest.raises(ValueError, match="request_id"):
        api.execute(cid, GameCommand.model_validate({**body.model_dump(), "action": {"kind": "check", "check_id": "perception"}}))
    short = run(api, cid, "short", kind="short_rest", hit_dice=1)
    state = api.store.campaign(cid)["state"]
    assert state["hero"]["hp"] == min(12, 7 + short["result"]["healed"])
    assert state["nat20"]["hit_dice"]["dice_remaining"] == 0
    assert state["nat20"]["hero"]["custom_counters"]["feature_use:second-wind"]["spent"] == 1
    long = run(api, cid, "long", kind="long_rest", food_water=False)
    state = api.store.campaign(cid)["state"]
    assert long["result"]["hp_current"] == state["hero"]["hp"] == 12
    assert state["hero"]["hit_dice_available"] == 1
    assert state["nat20"]["hero"]["custom_counters"]["feature_use:second-wind"]["spent"] == 0
    assert state["elapsed_minutes"] == 540
    with pytest.raises(ValueError, match="24 hours"):
        run(api, cid, "long-too-soon", kind="long_rest", food_water=False)
    assert len(api.store.history(cid)) == 3


def test_full_short_combat_and_actual_save_restart_continue(game):
    api, cid = game
    run(api, cid, "recover", kind="long_rest", food_water=False)
    started = run(api, cid, "open", kind="start_combat", encounter_id="basic")
    assert any(event["type"] == "turn_started" for event in started["native_events"])
    run(api, cid, "approach", kind="move", position={"x": 3, "y": 1, "z": 0})
    attack = run(api, cid, "first-swing", kind="attack", target="goblin")
    roll = next(event for event in attack["native_events"] if event["type"] == "attack_rolled")
    assert roll["modifier"] == 5 and roll["is_hit"]
    assert next(event["amount"] for event in attack["native_events"] if event["type"] == "damage_applied") == 4
    checkpoint = api.store.save(cid, "Active Nat20 combat")
    saved = deepcopy(api.store.campaign(cid)["state"]["nat20"])
    native = saved["combat"]["view"]
    assert [actor["entity_id"] for actor in native["initiative"]] == ["hero", "goblin"]
    assert native["tracked_hp"] == {"hero": 12, "goblin": 6}
    assert native["current_turn_index"] == 1 and native["round_number"] == 1
    assert native["actor_zone"]["hero"] == "3,1"
    hero = next(actor for actor in native["initiative"] if actor["entity_id"] == "hero")
    assert hero["movement_remaining"] == 20 and not hero["action_available"]
    restarted = fresh_process_restore(api.store.path, cid)
    assert restarted["runtime"] == saved
    assert restarted["snapshot"] == api.snapshot(api.store.campaign(cid)).model_dump(mode="json")
    # Continue without restart, then reload the checkpoint and continue with a
    # fresh facade/process. Native events and state must be byte-equivalent.
    baseline = run(api, cid, "baseline-turn", kind="end_turn")
    expected = deepcopy(api.store.campaign(cid)["state"]["nat20"])
    api.store.restore(cid, checkpoint)
    api = Nat20GameEngine(Store(api.store.path))
    continued = run(api, cid, "continued-turn", kind="end_turn")
    assert continued["native_events"] == baseline["native_events"]
    assert api.store.campaign(cid)["state"]["nat20"] == expected
    enemy_roll = next(event for event in continued["native_events"] if event["type"] == "attack_rolled")
    assert enemy_roll["attacker_id"] == "goblin" and enemy_roll["modifier"] == 4
    for index in range(16):
        view = api.store.campaign(cid)["state"]["nat20"]["combat"]["view"]
        if view["tracked_hp"]["goblin"] == 0:
            break
        actor = view["initiative"][view["current_turn_index"]]["entity_id"]
        run(api, cid, f"finish-{index}", **({"kind": "attack", "target": "goblin"} if actor == "hero" else {"kind": "end_turn"}))
    else:
        pytest.fail("seeded short combat did not finish")
    finished = run(api, cid, "close", kind="end_combat")
    assert finished["result"]["closed_combat"]["view"]["tracked_hp"]["goblin"] == 0
    assert finished["result"]["outcome"]["deaths"]
    assert not finished["snapshot"].combat.active
    assert api.store.campaign(cid)["state"]["nat20"]["combat"] is None
    assert Store(api.store.path).event(finished["journal"]["id"])["results"][0]["ai_proposal_applied"] is False
    assert fresh_process_restore(api.store.path, cid)["runtime"]["combat"] is None


def test_active_conditions_and_resources_survive_restart_and_refused_operations(tmp_path):
    api = Nat20GameEngine(Store(tmp_path / "condition-test.sqlite3"))
    cid = api.create_campaign("Poisoned", seed=5, conditions=("poisoned",), second_wind_spent=1)["id"]
    run(api, cid, "open", kind="start_combat", encounter_id="basic")
    run(api, cid, "move", kind="move", position={"x": 3, "y": 1, "z": 0})
    saved = deepcopy(api.store.campaign(cid))
    restored = fresh_process_restore(api.store.path, cid)
    assert restored["runtime"] == saved["state"]["nat20"]
    assert restored["runtime"]["combat"]["view"]["active_conditions"]["hero"] == ["poisoned"]
    assert restored["runtime"]["combat"]["view"]["custom_counters_by_entity"]["hero"]["feature_use:second-wind"]["spent"] == 1
    for id, action in [("no-rest", {"kind": "short_rest", "hit_dice": 1}),
                       ("no-escape", {"kind": "end_combat"}),
                       ("illegal-height", {"kind": "move", "position": {"x": 2, "y": 1, "z": 1}})]:
        with pytest.raises(ValueError):
            run(api, cid, id, **action)
        assert api.store.campaign(cid) == saved


def test_failed_commit_stale_command_undo_and_replay_leave_no_provider_state(game, monkeypatch):
    api, cid = game
    body = command(api, cid, "open", kind="start_combat", encounter_id="basic")
    before = api.store.campaign(cid)
    original = api.store.memory.ingest
    def fail(*args, **kwargs):
        raise RuntimeError("injected commit failure")
    monkeypatch.setattr(api.store.memory, "ingest", fail)
    with pytest.raises(RuntimeError, match="injected"):
        api.execute(cid, body)
    assert api.store.campaign(cid) == before and api.store.history(cid) == []
    monkeypatch.setattr(api.store.memory, "ingest", original)
    event = api.execute(cid, body)
    assert api.execute(cid, body)["journal"] == event["journal"]
    with pytest.raises(ValueError, match="Stale"):
        api.execute(cid, GameCommand(request_id="stale", expected_version=0, action={"kind": "end_turn"}))
    api.store.undo(cid)
    with pytest.raises(ValueError, match="abandoned"):
        api.execute(cid, body)
    assert api.restore(cid)["runtime"]["combat"] is None


def test_content_edition_and_command_capability_boundaries(game, monkeypatch):
    api, cid = game
    other = api.store.create("Existing Solo", Hero(name="Existing"), BUILTINS["dnd2024"])["id"]
    before = api.store.campaign(other)
    with pytest.raises(ValueError, match="explicitly"):
        run(api, other, "do-not-migrate", kind="check", check_id="athletics")
    assert api.store.campaign(other) == before
    for patch in [{"rules_version": "dnd2014"}, {"contract_version": "v0"}, {"dice": [20]}, {"state_delta": {"hp": 99}},
                  {"action": {"kind": "exec", "code": "raise Exception"}}]:
        with pytest.raises(ValueError):
            GameCommand.model_validate({"request_id": "bad", "expected_version": 0,
                                       "action": {"kind": "check", "check_id": "athletics"}, **patch})
    # A corrupt durable replay/read-model is refused, not normalized silently.
    run(api, cid, "open", kind="start_combat", encounter_id="basic")
    state = api.store.campaign(cid)["state"]["nat20"]
    altered = deepcopy(state)
    altered["combat"]["view"]["tracked_hp"]["hero"] -= 1
    with pytest.raises(ValueError, match="does not match replay"):
        api._worker({"mode": "verify", "state": altered})
    altered = deepcopy(state); altered["release_digest"] = "0" * 64
    with pytest.raises(ValueError, match="release changed"):
        api._worker({"mode": "verify", "state": altered})


def test_inventory_and_imported_provenance_are_distinct_from_execution():
    inventory = json.loads((ROOT / "integration/nat20/pack-inventory.json").read_text(encoding="utf-8"))
    manifest = json.loads((CONTENT / "manifest.json").read_text(encoding="utf-8"))
    assert len(inventory["packs"]) == 40 and len(manifest["documents"]) == 12
    assert all({"source", "rules_version", "locale", "license", "provenance", "import_status"} <= set(pack) for pack in inventory["packs"])
    foundry2024 = [pack for pack in inventory["packs"] if pack["rules_version"] == "dnd2024"]
    assert len(foundry2024) == 9
    laaru = [pack for pack in inventory["packs"] if pack["id"].startswith("laaru.")]
    assert laaru and all(pack["rules_version"] == "mixed_2014_homebrew" and pack["imported_documents"] == 0 for pack in laaru)
    assert inventory["components"][0]["rules_version"] == "dnd2014"
    assert not inventory["components"][0]["executed"]
    loader, fingerprint, profile = release()
    assert len(fingerprint) == 64 and profile["attack_bonus"] == 4 and profile["damage_ability_bonus"] == 2
    assert loader.get_spell("wish") is None
    assert loader.get_class("wizard") is None
    assert all(doc["rules_version"] == "dnd2024" and doc["license"] == "CC-BY-4.0" for doc in manifest["documents"])


def test_native_goblin_hit_uses_projected_bonus_and_damage(tmp_path):
    api = Nat20GameEngine(Store(tmp_path / "goblin-damage.sqlite3"))
    cid = api.create_campaign("Damage fixture", seed=3)["id"]
    run(api, cid, "open", kind="start_combat", encounter_id="basic")
    turn = run(api, cid, "goblin-turn", kind="end_turn")
    roll = next(event for event in turn["native_events"] if event["type"] == "attack_rolled")
    damage = next(event for event in turn["native_events"] if event["type"] == "damage_applied")
    assert roll["attacker_id"] == "goblin" and roll["natural"] == 18 and roll["modifier"] == 4
    assert damage["target_id"] == "hero" and damage["amount"] == 4  # native d6=2 + DEX=2
    assert api.store.campaign(cid)["state"]["hero"]["hp"] == 8
