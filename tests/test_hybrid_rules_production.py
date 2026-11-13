"""Real application routes, on fresh DBs only; no activation of user campaigns."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
import random
import socket

from fastapi.testclient import TestClient
import pytest

from solo.app import create_app
from solo.contracts import Hero
from solo.hybrid_rules import CheckCommand, verified_pack
import solo.hybrid_rules as hybrid
from solo.llm import MockLLM
from solo.rulesets import BUILTINS
from solo.storage import START, Store


class LocalRandom(random.Random):
    def __init__(self):
        super().__init__(1)
        self.calls = 0

    def randint(self, low, high):
        self.calls += 1
        return super().randint(low, high)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original = socket.socket.connect
    def denied(sock, address):
        # Windows asyncio creates its internal socketpair via loopback TCP.
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original(sock, address)
        raise AssertionError("network forbidden in production bridge tests")
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.fixture
def live(tmp_path, monkeypatch):
    llm = MockLLM()
    app = create_app(tmp_path / "production-test.sqlite3", llm=llm)
    store = app.state.store
    state = deepcopy(START)
    state["future_secret"] = "PRIVATE_FUTURE_713"
    hero = Hero(name="Bridge test", background="PRIVATE_BACKGROUND_921")
    campaign = store.create("Bridge test", hero, BUILTINS["dnd2024"], initial_state=state)["id"]
    other = store.create("Other campaign", hero, BUILTINS["dnd2024"])["id"]
    rng = LocalRandom()
    monkeypatch.setattr("solo.rulesets.secrets.SystemRandom", lambda: rng)
    with TestClient(app) as client:
        yield app, client, campaign, other, rng, llm
    assert llm.metrics == []


def url(campaign, suffix):
    return f"/api/campaigns/{campaign}/hybrid-rules/{suffix}"


def authorize(app, client, campaign):
    assert client.post("/api/advanced/session", json={"confirm": True}).status_code == 200
    pack = client.get("/api/hybrid-rules/verified")
    assert pack.status_code == 200
    request = {"confirm": True, "expected_version": app.state.store.campaign(campaign)["version"],
               "rules_family": "dnd2024", "rule_pack_digest": pack.json()["rule_pack_digest"]}
    response = client.post(url(campaign, "activation"), json=request)
    assert response.status_code == 200, response.text
    return client.get(url(campaign, "context")).json()


def command(context, request_id="check", **changes):
    body = {"confirm": True, "request_id": request_id, "expected_version": context["state_version"],
            "rules_family": "dnd2024", "rule_pack_digest": context["rule_pack_digest"],
            "ruleset_digest": context["ruleset_digest"], "operation": "ability_check",
            "ability": "strength", "dc": 15, "advantage_sources": 1, "disadvantage_sources": 0}
    body.update(changes)
    return body


def test_routes_are_registered_but_inactive_by_default(live):
    app, client, cid, _, rng, _ = live
    before = app.state.store.campaign(cid)
    assert client.get("/api/hybrid-rules/verified").status_code == 403
    assert client.get(url(cid, "context")).status_code == 403
    assert client.post("/api/advanced/session", json={"confirm": True}).status_code == 200
    pack = client.get("/api/hybrid-rules/verified").json()
    assert pack["default_active"] is False
    assert {row["id"] for row in pack["rules"]} == ({"dnd2024.d20." + name for name in ("advantage", "disadvantage", "cancellation")}
        | {"phb2024.phb01." + name for name in hybrid.CHAPTER_BINDINGS})
    assert pack["operations"] == ["ability_check", "skill_check", "saving_throw"]
    assert client.get(url(cid, "context")).status_code == 403
    assert app.state.store.campaign(cid) == before and rng.calls == 0


def test_real_http_roll_event_save_and_reopen_without_private_data(live):
    app, client, cid, _, rng, _ = live
    before = app.state.store.campaign(cid)
    context = authorize(app, client, cid)
    assert app.state.store.campaign(cid) == before  # activation is a session grant
    response = client.post(url(cid, "checks"), json=command(context, advantage_sources=2))
    assert response.status_code == 200, response.text
    public = response.json()
    assert public["result"]["dice"] == [5, 19] and public["result"]["total"] == 21
    assert public["result"]["success"] is True and rng.calls == 2
    assert app.state.store.campaign(cid)["version"] == 1
    assert app.state.store.campaign(cid)["state"]["turn"] == before["state"]["turn"] + 1
    for payload in (context, public):
        text = json.dumps(payload)
        assert "PRIVATE_" not in text and "before_state" not in text and "after_state" not in text
        assert "future_secret" not in text and "background" not in text and "explanation" not in text
    assert set(context["actor"]) == {"name", "level", "abilities"}
    saved = client.post(f"/api/campaigns/{cid}/saves", json={"name": "Production bridge checkpoint"})
    assert saved.status_code == 200
    reopened = Store(app.state.store.path)
    event = reopened.event(public["id"])
    assert event["results"][0]["kind"] == "hybrid_ability_check"
    assert event["results"][0]["ai_proposal_applied"] is False
    assert event["after_state"]["future_secret"] == "PRIVATE_FUTURE_713"
    assert saved.json()["id"] in [row["id"] for row in reopened.saves(cid)]
    assert client.get("/api/ai/costs").json()["calls"] == []


def test_session_and_campaign_acl_is_enforced_on_reads_and_replays(live):
    app, client, cid, other, rng, _ = live
    body = command(authorize(app, client, cid))
    first = client.post(url(cid, "checks"), json=body)
    assert first.status_code == 200
    assert client.get(url(other, "context")).status_code == 403
    assert client.post(url(other, "checks"), json=body).status_code == 403
    with TestClient(app) as second:
        assert second.get(url(cid, "context")).status_code == 403
        assert second.post("/api/advanced/session", json={"confirm": True}).status_code == 200
        assert second.get(url(cid, "context")).status_code == 403
        assert second.post(url(cid, "checks"), json=body).status_code == 403
        pack = second.get("/api/hybrid-rules/verified").json()
        enabled = second.post(url(cid, "activation"), json={"confirm": True, "expected_version": 1,
                             "rules_family": "dnd2024", "rule_pack_digest": pack["rule_pack_digest"]})
        assert enabled.status_code == 200
        assert second.post(url(cid, "checks"), json=body).status_code == 409  # signature is principal-bound
    assert rng.calls == 2 and len(app.state.store.history(cid)) == 1


@pytest.mark.parametrize("revoke", ["grant", "session", "expiry"])
def test_revoked_access_never_returns_an_old_event(live, revoke):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    assert client.post(url(cid, "checks"), json=body).status_code == 200
    before = app.state.store.campaign(cid)
    if revoke == "grant":
        assert client.delete(url(cid, "activation")).status_code == 200
    elif revoke == "session":
        assert client.delete("/api/advanced/session").status_code == 200
    else:
        app.state.advanced_sessions[client.cookies.get("solo_advanced")] = 0
    assert client.get(url(cid, "context")).status_code == 403
    assert client.post(url(cid, "checks"), json=body).status_code == 403
    assert app.state.store.campaign(cid) == before and rng.calls == 2


def test_exact_replay_and_changed_command_replay(live):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    first = client.post(url(cid, "checks"), json=body)
    assert first.status_code == 200
    before = app.state.store.campaign(cid)
    assert client.post(url(cid, "checks"), json=body).json() == first.json()
    assert client.post(url(cid, "checks"), json={**body, "dc": 12}).status_code == 409
    assert rng.calls == 2 and app.state.store.campaign(cid) == before


@pytest.mark.parametrize("advantages,disadvantages,mode,draws", [
    (0, 0, "обычный", 1), (3, 0, "преимущество", 2), (0, 3, "помеха", 2),
    (1, 1, "обычный", 1), (3, 1, "обычный", 1), (1, 3, "обычный", 1),
])
def test_causes_are_bound_to_existing_engine(live, advantages, disadvantages, mode, draws):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid), advantage_sources=advantages, disadvantage_sources=disadvantages)
    response = client.post(url(cid, "checks"), json=body)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["mode"] == mode and rng.calls == draws


def test_internal_condition_stays_in_engine(live):
    app, client, _, _, rng, _ = live
    cid = app.state.store.create("Poisoned", Hero(name="Test", conditions=["poisoned"]), BUILTINS["dnd2024"])["id"]
    body = command(authorize(app, client, cid))
    response = client.post(url(cid, "checks"), json=body)
    assert response.status_code == 200
    assert response.json()["result"]["mode"] == "обычный" and rng.calls == 1


@pytest.mark.parametrize("field,value", [("expected_version", 99), ("ruleset_digest", "f" * 64), ("rule_pack_digest", "f" * 64)])
def test_versions_and_fingerprints_rejected_before_rng(live, field, value):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid), **{field: value})
    before = app.state.store.campaign(cid)
    assert client.post(url(cid, "checks"), json=body).status_code == 409
    assert app.state.store.campaign(cid) == before and rng.calls == 0
    assert app.state.store.history(cid) == []


def test_edition_is_checked_in_data_and_campaign(live):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid), rules_family="dnd2014")
    assert client.post(url(cid, "checks"), json=body).status_code == 422
    old = app.state.store.create("2014", Hero(name="Test"), BUILTINS["dnd2014"])["id"]
    pack = client.get("/api/hybrid-rules/verified").json()
    response = client.post(url(old, "activation"), json={"confirm": True, "expected_version": 0,
                           "rules_family": "dnd2024", "rule_pack_digest": pack["rule_pack_digest"]})
    assert response.status_code == 409
    assert client.get(url(old, "context")).status_code == 403 and rng.calls == 0


@pytest.mark.parametrize("failure", ["definition_pin", "engine_pin", "dependency"])
def test_changed_or_missing_dependencies_fail_closed(live, monkeypatch, failure):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    before = app.state.store.campaign(cid)
    if failure == "definition_pin":
        monkeypatch.setattr(hybrid, "PINS", (("advantage", "f" * 64), *hybrid.PINS[1:]))
    elif failure == "engine_pin":
        monkeypatch.setattr(hybrid, "ENGINE_SHA256", "f" * 64)
    else:
        monkeypatch.setattr(hybrid, "status", lambda *args: "unsupported")
    assert client.post(url(cid, "checks"), json=body).status_code == 409
    assert app.state.store.campaign(cid) == before and rng.calls == 0


@pytest.mark.parametrize("operation", ["eval", "exec", "wish_stress", "registered_bonus_action"])
def test_unregistered_operations_are_rejected_by_http_contract(live, operation):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid), operation=operation)
    assert client.post(url(cid, "checks"), json=body).status_code == 422
    assert app.state.store.history(cid) == [] and rng.calls == 0


@pytest.mark.parametrize("extra", [{"dice": [20]}, {"rng": "code"}, {"state_delta": {"hero.hp": 999}},
                                    {"definition": {"effects": [{"op": "exec"}]}}])
def test_raw_ai_mechanics_and_definition_uploads_are_not_accepted(live, extra):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid), **extra)
    assert client.post(url(cid, "checks"), json=body).status_code == 422
    assert app.state.store.history(cid) == [] and rng.calls == 0


def test_proposal_never_commits_or_executes_text(live, tmp_path):
    app, client, cid, _, rng, _ = live
    context = authorize(app, client, cid)
    marker = tmp_path / "must-not-exist"
    body = {"expected_version": context["state_version"], "rules_family": "dnd2024",
            "rule_pack_digest": context["rule_pack_digest"], "ruleset_digest": context["ruleset_digest"],
            "operation": "ability_check", "ability": "strength",
            "explanation": f"__import__('pathlib').Path({str(marker)!r}).write_text('unsafe')"}
    before = app.state.store.campaign(cid)
    response = client.post(url(cid, "proposals"), json=body)
    assert response.status_code == 200 and response.json()["commit_allowed"] is False
    assert client.post(url(cid, "checks"), json=response.json()).status_code == 422
    assert app.state.store.campaign(cid) == before and app.state.store.history(cid) == []
    assert rng.calls == 0 and not marker.exists()


@pytest.mark.parametrize("field,value", [("confirm", False), ("confirm", 1), ("advantage_sources", True), ("dc", "15")])
def test_explicit_confirmation_and_strict_types(live, field, value):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid), **{field: value})
    response = client.post(url(cid, "checks"), json=body)
    assert response.status_code == (409 if field == "confirm" and value is False else 422)
    assert app.state.store.history(cid) == [] and rng.calls == 0


def test_origin_policy_applies_to_new_routes(live):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    assert client.post(url(cid, "checks"), json=body, headers={"origin": "https://untrusted.example"}).status_code == 403
    assert app.state.store.history(cid) == [] and rng.calls == 0


def test_rollback_and_retry_use_existing_transaction(live, monkeypatch):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    before = app.state.store.campaign(cid)
    original = app.state.store.memory.ingest
    def fail(*args, **kwargs):
        raise RuntimeError("injected canonical persistence failure")
    monkeypatch.setattr(app.state.store.memory, "ingest", fail)
    with pytest.raises(RuntimeError, match="persistence failure"):
        client.post(url(cid, "checks"), json=body)
    assert app.state.store.campaign(cid) == before and app.state.store.history(cid) == []
    assert app.state.store.request(cid, body["request_id"]) is None
    monkeypatch.setattr(app.state.store.memory, "ingest", original)
    assert client.post(url(cid, "checks"), json=body).status_code == 200
    assert len(app.state.store.history(cid)) == 1


def test_replay_after_undo_is_rejected(live):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    assert client.post(url(cid, "checks"), json=body).status_code == 200
    assert client.post(f"/api/campaigns/{cid}/undo").status_code == 200
    before = app.state.store.campaign(cid)
    assert client.post(url(cid, "checks"), json=body).status_code == 409
    assert app.state.store.campaign(cid) == before and rng.calls == 2


def test_grants_do_not_survive_restart(live):
    app, client, cid, _, rng, _ = live
    authorize(app, client, cid)
    restarted = create_app(app.state.store.path, llm=MockLLM())
    with TestClient(restarted) as next_client:
        assert next_client.post("/api/advanced/session", json={"confirm": True}).status_code == 200
        assert next_client.get(url(cid, "context")).status_code == 403
    assert rng.calls == 0


def test_binding_has_exactly_the_checked_dependencies():
    definitions, fingerprint = verified_pack()
    assert len(definitions) == 9 and len(fingerprint) == 64
    assert all(row.edition == "dnd2024" for row in definitions)
    assert all(row.dependencies == ("checks.ability",) for row in definitions[:3])
    ids = {row.id for row in definitions}
    assert all(dep in ids or dep == "checks.ability" for row in definitions for dep in row.dependencies)


@pytest.mark.parametrize("change,expected", [({"confirm": False}, 409), ({"expected_version": 99}, 409),
                                           ({"rule_pack_digest": "f" * 64}, 409)])
def test_invalid_activation_never_creates_a_grant(live, change, expected):
    app, client, cid, _, rng, _ = live
    assert client.post("/api/advanced/session", json={"confirm": True}).status_code == 200
    pack = client.get("/api/hybrid-rules/verified").json()
    body = {"confirm": True, "expected_version": 0, "rules_family": "dnd2024", "rule_pack_digest": pack["rule_pack_digest"], **change}
    assert client.post(url(cid, "activation"), json=body).status_code == expected
    assert client.get(url(cid, "context")).status_code == 403
    assert app.state.store.history(cid) == [] and rng.calls == 0


def test_missing_definition_fails_closed(live, monkeypatch, tmp_path):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    monkeypatch.setattr(hybrid, "DEFINITION_ROOT", tmp_path / "not-a-rule-pack")
    response = client.post(url(cid, "checks"), json=body)
    assert response.status_code == 409
    assert str(tmp_path) not in response.text
    assert app.state.store.history(cid) == [] and rng.calls == 0


def test_ruleset_version_change_invalidates_existing_grant(live):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    def changed(state):
        state["ruleset"]["version"] += 1
        return [{"type": "scripted", "kind": "test_profile_change"}]
    app.state.store.record_scenario(cid, "change-profile", "Trusted test profile change", changed)
    before = app.state.store.campaign(cid)
    assert client.get(url(cid, "context")).status_code == 409
    assert client.post(url(cid, "checks"), json=body).status_code == 409
    assert app.state.store.campaign(cid) == before and rng.calls == 0


@pytest.mark.parametrize("replay", [False, True])
def test_acl_rechecked_inside_commit_boundary(live, monkeypatch, replay):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    if replay:
        assert client.post(url(cid, "checks"), json=body).status_code == 200
    before = app.state.store.campaign(cid)
    calls = rng.calls
    original = app.state.store.engine_event
    def revoke_and_enter(*args, **kwargs):
        app.state.hybrid_rules._grants.clear()
        return original(*args, **kwargs)
    monkeypatch.setattr(app.state.store, "engine_event", revoke_and_enter)
    assert client.post(url(cid, "checks"), json=body).status_code == 403
    assert app.state.store.campaign(cid) == before and rng.calls == calls
    assert len(app.state.store.history(cid)) == int(replay)


def test_concurrent_commands_cannot_both_commit_the_same_state_version(live):
    app, client, cid, _, rng, _ = live
    context = authorize(app, client, cid)
    def submit(index):
        return client.post(url(cid, "checks"), json=command(context, request_id=f"concurrent-{index}")).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, (1, 2)))
    assert sorted(results) == [200, 409]
    assert len(app.state.store.history(cid)) == 1 and rng.calls == 2


def test_native_command_copy_cannot_bypass_operation_validation(live):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid))
    forged = CheckCommand.model_validate(body).model_copy(update={"operation": "exec"})
    with pytest.raises(ValueError):
        app.state.hybrid_rules.execute(client.cookies.get("solo_advanced"), cid, forged)
    assert app.state.store.history(cid) == [] and rng.calls == 0
