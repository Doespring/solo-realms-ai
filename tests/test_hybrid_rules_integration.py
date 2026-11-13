"""Only a fresh temporary campaign; no HTTP app, provider or PDF execution."""
from dataclasses import replace
import random
import socket

import pytest

from solo.contracts import Hero
from solo.rulesets import active, digest
from solo.storage import Store
# The runtime module of `rules_data` belonged to an earlier, withdrawn build of
# the hybrid calculator: it is in no commit and no production module imports it.
# Only these two vector/integration suites did. The data those rules describe is
# present (`rules_data/v1|v2/chapters/phb-01`) and lives on through
# `solo/hybrid_rules.py`; the code that ran the vectors is gone. Skipping keeps
# the vectors as history instead of deleting them and calling it green.
rules_data_runtime = pytest.importorskip(
    "rules_data.runtime.protocol",
    reason="rules_data.runtime belonged to a withdrawn build; vectors kept as history",
)
from rules_data.runtime.protocol import seal_request, validate_response
from rules_data.testing.engine_adapter import IsolatedCheckAdapter
from rules_data.testing.schema_validation import V2, load_json, validate


class QueueDice:
    def __init__(self, *values):
        self.values = iter(values)
        self.calls = 0

    def randint(self, low, high):
        assert (low, high) == (1, 20)
        self.calls += 1
        value = next(self.values)
        assert low <= value <= high
        return value


class CountingRandom(random.Random):
    def __init__(self, seed):
        super().__init__(seed)
        self.calls = 0

    def randint(self, low, high):
        self.calls += 1
        return super().randint(low, high)


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network is forbidden in the isolated rules test stand")
    monkeypatch.setattr(socket.socket, "connect", denied)


def test_complete_local_roll_event_checkpoint_and_reopen():
    rng = CountingRandom(1)
    with IsolatedCheckAdapter(rng=rng) as adapter:
        root = adapter.root
        command = adapter.command("complete", advantage_sources=2)
        before = adapter.store.campaign(adapter.campaign_id)
        event = adapter.execute(command)
        result = event["results"][0]["check"]
        assert result["dice"] == [5, 19]
        assert result["selected"] == 19 and result["bonus"] == 2
        assert result["total"] == 21 and result["dc"] == 15 and result["success"] is True
        assert result["ruleset"] == {"id": "dnd2024", "version": 1}
        assert rng.calls == 2
        after = adapter.store.campaign(adapter.campaign_id)
        assert after["version"] == before["version"] + 1
        assert after["state"]["turn"] == before["state"]["turn"] + 1
        assert after["state"]["hero"] == before["state"]["hero"]
        assert event["before_state"] == before["state"]
        assert event["after_state"] == after["state"]
        checkpoint = adapter.store.save(adapter.campaign_id, "Verified d20 checkpoint")
        # Windows refuses an immediate rename if backup left sqlite3 open.
        backup = adapter.root / "backups" / "latest.sqlite3"
        moved = backup.rename(backup.with_name("verified.sqlite3"))
        moved.rename(backup)
        reopened = Store(adapter.store.path)
        assert reopened.event(event["id"]) == event
        assert reopened.campaign(adapter.campaign_id)["state"] == after["state"]
        assert checkpoint in [item["id"] for item in reopened.saves(adapter.campaign_id)]
        assert event["results"][0]["ai_proposal_applied"] is False
    assert not root.exists()


@pytest.mark.parametrize("advantages,disadvantages,draws,selected,mode", [
    (0, 0, [7], 7, "обычный"),
    (1, 0, [3, 18], 18, "преимущество"),
    (0, 1, [3, 18], 3, "помеха"),
    (3, 0, [3, 18], 18, "преимущество"),
    (0, 3, [3, 18], 3, "помеха"),
    (1, 1, [11], 11, "обычный"),
    (3, 1, [11], 11, "обычный"),
    (1, 3, [11], 11, "обычный"),
])
def test_causes_resolve_through_existing_engine(advantages, disadvantages, draws, selected, mode):
    rng = QueueDice(*draws)
    with IsolatedCheckAdapter(rng=rng) as adapter:
        event = adapter.execute(adapter.command("causes", advantage_sources=advantages, disadvantage_sources=disadvantages))
        result = event["results"][0]["check"]
        assert result["selected"] == selected and result["mode"] == mode
        assert result["dice"] == draws and rng.calls == len(draws)


def test_internal_poisoned_source_cancels_explicit_advantage():
    rng = QueueDice(11)
    with IsolatedCheckAdapter(hero=Hero(name="Poisoned test", conditions=["poisoned"]), rng=rng) as adapter:
        event = adapter.execute(adapter.command("poisoned", advantage_sources=1))
        assert event["results"][0]["check"]["mode"] == "обычный"
        assert rng.calls == 1


def test_exact_replay_is_saved_event_without_a_second_roll():
    rng = QueueDice(3, 18)
    with IsolatedCheckAdapter(rng=rng) as adapter:
        command = adapter.command("repeat", advantage_sources=1)
        event = adapter.execute(command)
        state = adapter.store.campaign(adapter.campaign_id)
        assert adapter.execute(command) == event
        assert adapter.store.campaign(adapter.campaign_id) == state
        assert rng.calls == 2 and len(adapter.store.history(adapter.campaign_id)) == 1
        with pytest.raises(ValueError, match="request_id"):
            adapter.execute(replace(command, dc=12))
        assert rng.calls == 2


def test_stale_state_rejected_before_roll_or_event():
    rng = QueueDice(11)
    with IsolatedCheckAdapter(rng=rng) as adapter:
        stale = adapter.command("stale")
        adapter.execute(adapter.command("advance"))
        before = adapter.store.campaign(adapter.campaign_id)
        with pytest.raises(ValueError, match="stale state"):
            adapter.execute(stale)
        assert adapter.store.campaign(adapter.campaign_id) == before
        assert rng.calls == 1 and len(adapter.store.history(adapter.campaign_id)) == 1


@pytest.mark.parametrize("target", ["command", "campaign"])
def test_mismatched_edition_rejected_without_writes(target):
    rng = QueueDice()
    with IsolatedCheckAdapter(ruleset_id="dnd2014" if target == "campaign" else "dnd2024", rng=rng) as adapter:
        command = adapter.command("edition")
        if target == "command":
            command = replace(command, rules_family="dnd2014")
        before = adapter.store.campaign(adapter.campaign_id)
        with pytest.raises(ValueError, match="edition mismatch"):
            adapter.execute(command)
        assert adapter.store.campaign(adapter.campaign_id) == before
        assert adapter.store.history(adapter.campaign_id) == [] and rng.calls == 0


def test_ruleset_fingerprint_rejected_before_roll():
    with IsolatedCheckAdapter(rng=QueueDice()) as adapter:
        command = replace(adapter.command("fingerprint"), ruleset_digest="f" * 64)
        with pytest.raises(ValueError, match="fingerprint mismatch"):
            adapter.execute(command)
        assert adapter.store.history(adapter.campaign_id) == [] and adapter.rng.calls == 0


@pytest.mark.parametrize("operation", ["eval", "exec", "wish_stress", "registered_bonus_action"])
def test_unregistered_operations_are_data_and_never_executed(operation):
    with IsolatedCheckAdapter(rng=QueueDice()) as adapter:
        before = adapter.store.campaign(adapter.campaign_id)
        with pytest.raises(ValueError, match="unregistered"):
            adapter.execute(replace(adapter.command("forbidden"), operation=operation))
        assert adapter.store.campaign(adapter.campaign_id) == before
        assert adapter.store.history(adapter.campaign_id) == [] and adapter.rng.calls == 0


def test_ai_decision_remains_proposal_until_explicit_engine_command():
    request = load_json(V2 / "tests/messages/dmg.request.json")
    response = load_json(V2 / "tests/messages/dmg.response.json")
    with IsolatedCheckAdapter(rng=QueueDice(11)) as adapter:
        before = adapter.store.campaign(adapter.campaign_id)
        request["state_version"] = before["version"]
        request["ruleset_digest"] = digest(active(before["state"]).model_dump())
        request = seal_request(request)
        for field in ("state_version", "ruleset_digest", "context_hash"):
            response[field] = request[field]
        validate(request)
        validate(response)
        proposal = validate_response(request, response, current_state_version=before["version"],
                                     current_ruleset_digest=request["ruleset_digest"], consumed_request_ids=())
        assert proposal["status"] == "validated_proposal" and proposal["commit_allowed"] is False
        for untrusted in (response, proposal):
            with pytest.raises(ValueError, match="proposal"):
                adapter.execute(untrusted)
        assert adapter.store.campaign(adapter.campaign_id) == before
        assert adapter.store.history(adapter.campaign_id) == []
        # Separately authorized test command; no AI fields are copied into it.
        event = adapter.execute(adapter.command("trusted_check"))
        assert event["results"][0]["ai_proposal_applied"] is False


def test_ai_text_is_never_executed(tmp_path):
    marker = tmp_path / "must-not-exist.txt"
    request = load_json(V2 / "tests/messages/dmg.request.json")
    response = load_json(V2 / "tests/messages/dmg.response.json")
    response["rationale"] = f"__import__('pathlib').Path({str(marker)!r}).write_text('unsafe')"
    validate(request)
    validate(response)
    proposal = validate_response(request, response, current_state_version=1,
                                 current_ruleset_digest=request["ruleset_digest"], consumed_request_ids=())
    with IsolatedCheckAdapter(rng=QueueDice()) as adapter:
        with pytest.raises(ValueError, match="proposal"):
            adapter.execute(proposal)
        assert adapter.store.history(adapter.campaign_id) == []
    assert not marker.exists()


def test_adapter_refuses_database_outside_owned_sandbox(tmp_path):
    outside = tmp_path / "outside.sqlite3"
    with IsolatedCheckAdapter(rng=QueueDice()) as adapter:
        command = adapter.command("wrong_database")
        adapter.store.path = outside
        with pytest.raises(ValueError, match="own temporary database"):
            adapter.execute(command)
        assert not outside.exists()


def test_transaction_failure_rolls_back_state_action_and_event(monkeypatch):
    with IsolatedCheckAdapter(rng=QueueDice(11)) as adapter:
        before = adapter.store.campaign(adapter.campaign_id)
        def fail_commit(*args, **kwargs):
            raise RuntimeError("injected persistence failure")
        monkeypatch.setattr(adapter.store.memory, "ingest", fail_commit)
        with pytest.raises(RuntimeError, match="persistence failure"):
            adapter.execute(adapter.command("rollback"))
        assert adapter.store.campaign(adapter.campaign_id) == before
        assert adapter.store.history(adapter.campaign_id) == []
        assert adapter.store.request(adapter.campaign_id, "rollback") is None


def test_replay_after_undo_does_not_resurrect_abandoned_event():
    with IsolatedCheckAdapter(rng=QueueDice(11)) as adapter:
        command = adapter.command("undone")
        adapter.execute(command)
        adapter.store.undo(adapter.campaign_id)
        before = adapter.store.campaign(adapter.campaign_id)
        with pytest.raises(ValueError, match="abandoned"):
            adapter.execute(command)
        assert adapter.store.campaign(adapter.campaign_id) == before and adapter.rng.calls == 1
