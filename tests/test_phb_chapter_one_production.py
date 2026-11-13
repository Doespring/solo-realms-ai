"""Real registered routes, existing RNG/transactions/checkpoints, fresh DBs only."""
import json

import pytest

from solo.contracts import Hero
import solo.hybrid_rules as bridge
from solo.rulesets import BUILTINS
from solo.storage import Store
from test_hybrid_rules_production import authorize, command, live, no_network, url


def body_for(context, operation, **changes):
    fields = {"operation": operation, "ability": "strength"}
    if operation == "skill_check":
        fields["skill"] = "athletics"
    fields.update(changes)
    return command(context, **fields)


@pytest.mark.parametrize("operation,bonus", [("skill_check", 8), ("saving_throw", 5)])
def test_new_operation_roll_event_checkpoint_reopen_and_visibility(live, operation, bonus):
    app, client, _, _, rng, llm = live
    hero = Hero(name="PHB level five", level=5, skill_proficiencies=["athletics"],
                expertise=["athletics"], saving_throw_proficiencies=["strength"],
                background="PRIVATE_BIO", features=["PRIVATE_FEATURE"])
    cid = app.state.store.create("PHB isolated", hero, BUILTINS["dnd2024"])["id"]
    context = authorize(app, client, cid)
    before = app.state.store.campaign(cid)
    body = body_for(context, operation)
    response = client.post(url(cid, "checks"), json=body)
    assert response.status_code == 200, response.text
    event = response.json()
    assert event["operation"] == operation and event["result"]["dice"] == [5, 19]
    assert event["result"]["bonus"] == bonus and event["result"]["total"] == 19 + bonus
    assert event["result"]["success"] is True and rng.calls == 2
    assert "phb2024.phb01." + operation in event["rule_ids"]
    if operation == "skill_check":
        assert event["skill"] == "athletics"
    for value in (event, context):
        serialized = json.dumps(value)
        assert "PRIVATE_" not in serialized and "background" not in serialized
        assert "before_state" not in serialized and "after_state" not in serialized
    assert app.state.store.campaign(cid)["state"]["hero"] == before["state"]["hero"]
    assert client.post(url(cid, "checks"), json=body).json() == event and rng.calls == 2
    saved = client.post(f"/api/campaigns/{cid}/saves", json={"name": "PHB verified checkpoint"})
    assert saved.status_code == 200
    reopened = Store(app.state.store.path)
    stored = reopened.event(event["id"])
    assert stored["results"][0]["operation"] == operation
    assert stored["results"][0]["ai_proposal_applied"] is False
    assert saved.json()["id"] in {row["id"] for row in reopened.saves(cid)}
    assert reopened.campaign(cid)["version"] == 1 and llm.metrics == []


@pytest.mark.parametrize("operation", ["skill_check", "saving_throw"])
@pytest.mark.parametrize("advantages,disadvantages,expected,draws", [
    (4, 1, "обычный", 1), (1, 4, "обычный", 1), (0, 4, "помеха", 2), (4, 0, "преимущество", 2),
])
def test_causes_use_same_existing_d20_for_new_operations(live, operation, advantages, disadvantages, expected, draws):
    app, client, cid, _, rng, _ = live
    context = authorize(app, client, cid)
    response = client.post(url(cid, "checks"), json=body_for(context, operation,
                            advantage_sources=advantages, disadvantage_sources=disadvantages))
    assert response.status_code == 200
    assert response.json()["result"]["mode"] == expected and rng.calls == draws


@pytest.mark.parametrize("operation", ["skill_check", "saving_throw"])
@pytest.mark.parametrize("failure", ["campaign_acl", "stale", "edition", "fingerprint", "unconfirmed", "raw_delta"])
def test_new_operations_reject_before_rng_or_canonical_write(live, operation, failure):
    app, client, cid, other, rng, _ = live
    body = body_for(authorize(app, client, cid), operation)
    target, status = cid, 409
    if failure == "campaign_acl":
        target, status = other, 403
    elif failure == "stale":
        body["expected_version"] = 99
    elif failure == "edition":
        body["rules_family"], status = "dnd2014", 422
    elif failure == "fingerprint":
        body["ruleset_digest"] = "0" * 64
    elif failure == "unconfirmed":
        body["confirm"] = False
    else:
        body["state_delta"], status = {"hero.hp": 1000}, 422
    before = app.state.store.campaign(target)
    assert client.post(url(target, "checks"), json=body).status_code == status
    assert app.state.store.campaign(target) == before and rng.calls == 0
    assert app.state.store.history(target) == []


@pytest.mark.parametrize("operation", ["skill_check", "saving_throw"])
def test_new_operations_rollback_replay_change_and_undo(live, monkeypatch, operation):
    app, client, cid, _, rng, _ = live
    body = body_for(authorize(app, client, cid), operation)
    before = app.state.store.campaign(cid)
    original = app.state.store.memory.ingest
    def fail(*args, **kwargs):
        raise RuntimeError("PHB injected persistence failure")
    monkeypatch.setattr(app.state.store.memory, "ingest", fail)
    with pytest.raises(RuntimeError, match="PHB injected"):
        client.post(url(cid, "checks"), json=body)
    assert app.state.store.campaign(cid) == before and app.state.store.history(cid) == []
    assert app.state.store.request(cid, body["request_id"]) is None
    monkeypatch.setattr(app.state.store.memory, "ingest", original)
    event = client.post(url(cid, "checks"), json=body)
    assert event.status_code == 200
    calls = rng.calls
    assert client.post(url(cid, "checks"), json=body).json() == event.json()
    assert client.post(url(cid, "checks"), json={**body, "dc": 12}).status_code == 409
    assert rng.calls == calls
    assert client.post(f"/api/campaigns/{cid}/undo").status_code == 200
    undone = app.state.store.campaign(cid)
    assert undone["state"]["hero"] == before["state"]["hero"]
    assert client.post(url(cid, "checks"), json=body).status_code == 409
    assert app.state.store.campaign(cid) == undone and rng.calls == calls


@pytest.mark.parametrize("operation", ["skill_check", "saving_throw"])
def test_new_ai_proposals_remain_inert(live, tmp_path, operation):
    app, client, cid, _, rng, _ = live
    context = authorize(app, client, cid)
    marker = tmp_path / "proposal-must-not-execute"
    proposal = {key: context[key] for key in ("rules_family", "rule_pack_digest", "ruleset_digest")}
    proposal.update(expected_version=context["state_version"], operation=operation, ability="strength",
                    explanation=f"__import__('pathlib').Path({str(marker)!r}).touch()")
    if operation == "skill_check":
        proposal["skill"] = "athletics"
    before = app.state.store.campaign(cid)
    response = client.post(url(cid, "proposals"), json=proposal)
    assert response.status_code == 200 and response.json()["commit_allowed"] is False
    assert client.post(url(cid, "checks"), json=proposal).status_code == 422
    assert app.state.store.campaign(cid) == before and rng.calls == 0 and not marker.exists()


@pytest.mark.parametrize("changes", [
    {"operation": "skill_check", "skill": "unknown"},
    {"operation": "skill_check", "skill": "athletics", "ability": "charisma"},
    {"operation": "skill_check", "skill": "athletics", "proficient": True},
    {"operation": "saving_throw", "proficient": True},
    {"operation": "saving_throw", "skill": "athletics"},
    {"operation": "saving_throw", "voluntary_failure": True},
    {"operation": "saving_throw", "damage": 42},
])
def test_sheet_bindings_and_unsupported_consequences_cannot_be_overridden(live, changes):
    app, client, cid, _, rng, _ = live
    body = command(authorize(app, client, cid), **changes)
    assert client.post(url(cid, "checks"), json=body).status_code == 422
    assert rng.calls == 0 and app.state.store.history(cid) == []


@pytest.mark.parametrize("condition", ["paralyzed", "unconscious", "frightened", "blinded"])
def test_unsupported_condition_context_is_not_activated(live, condition):
    app, client, _, _, rng, _ = live
    cid = app.state.store.create("Unsupported context", Hero(name="Test", conditions=[condition]), BUILTINS["dnd2024"])["id"]
    assert client.post("/api/advanced/session", json={"confirm": True}).status_code == 200
    pack = client.get("/api/hybrid-rules/verified").json()
    body = {"confirm": True, "expected_version": 0, "rules_family": "dnd2024", "rule_pack_digest": pack["rule_pack_digest"]}
    assert client.post(url(cid, "activation"), json=body).status_code == 409
    assert client.get(url(cid, "context")).status_code == 403 and rng.calls == 0


@pytest.mark.parametrize("failure", ["chapter_pin", "chapter_missing"])
def test_chapter_source_release_fails_closed(live, monkeypatch, tmp_path, failure):
    app, client, cid, _, rng, _ = live
    body = body_for(authorize(app, client, cid), "saving_throw")
    if failure == "chapter_pin":
        monkeypatch.setattr(bridge, "CHAPTER_SHA256", "0" * 64)
    else:
        monkeypatch.setattr(bridge, "CHAPTER_PATH", tmp_path / "missing.json")
    assert client.post(url(cid, "checks"), json=body).status_code == 409
    assert rng.calls == 0 and app.state.store.history(cid) == []


def test_poisoned_saving_throw_does_not_inherit_ability_check_disadvantage(live):
    app, client, _, _, rng, _ = live
    cid = app.state.store.create("Poison", Hero(name="Save", conditions=["poisoned"]), BUILTINS["dnd2024"])["id"]
    body = body_for(authorize(app, client, cid), "saving_throw", advantage_sources=0)
    response = client.post(url(cid, "checks"), json=body)
    assert response.status_code == 200 and response.json()["result"]["mode"] == "обычный" and rng.calls == 1


def test_native_proposal_copy_cannot_bypass_validation(live):
    app, client, cid, _, rng, _ = live
    context = authorize(app, client, cid)
    proposal = bridge.CheckProposal(expected_version=context["state_version"], rules_family="dnd2024",
        rule_pack_digest=context["rule_pack_digest"], ruleset_digest=context["ruleset_digest"],
        operation="saving_throw", ability="strength", explanation="Test")
    forged = proposal.model_copy(update={"rules_family": "dnd2014"})
    with pytest.raises(ValueError):
        app.state.hybrid_rules.propose(client.cookies.get("solo_advanced"), cid, forged)
    assert rng.calls == 0 and app.state.store.history(cid) == []
