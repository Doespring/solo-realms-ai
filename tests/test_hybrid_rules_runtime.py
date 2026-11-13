"""Run the original 48 vectors and 13 schema expectations, offline and isolated."""
from copy import deepcopy
import socket

import pytest

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
from rules_data.runtime.classifier import classify
from rules_data.runtime.math_handlers import execute
from rules_data.runtime.protocol import seal_request, validate_response
from rules_data.testing.schema_validation import V2, load_json, patch_json, validate

CASES = load_json(V2 / "tests/runtime.cases.json")
MANIFEST = load_json(V2 / "tests/manifest.json")


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network is forbidden in the isolated rules test stand")
    monkeypatch.setattr(socket.socket, "connect", denied)


@pytest.mark.parametrize("case", CASES["classifier_cases"], ids=lambda case: case["id"])
def test_classifier_vector(case):
    def run():
        return classify(case["semantic_required"], case["formal_required"], case.get("dependency_modes", ()))
    if case.get("reject"):
        with pytest.raises(ValueError):
            run()
    else:
        assert run() == case["expected"]


@pytest.mark.parametrize("case", CASES["math_cases"], ids=lambda case: case["id"])
def test_math_vector(case):
    if case.get("reject"):
        with pytest.raises(ValueError):
            execute(case["op"], case["arguments"])
    else:
        result = execute(case["op"], case["arguments"])
        assert type(result) is type(case["expected"])
        assert result == case["expected"]


@pytest.mark.parametrize("case", CASES["protocol_cases"], ids=lambda case: case["id"])
def test_protocol_vector(case):
    pair = case.get("pair", "dmg")
    request = load_json(V2 / f"tests/messages/{pair}.request.json")
    response = load_json(V2 / ("tests/" + case["response_fixture"])) if "response_fixture" in case else load_json(V2 / f"tests/messages/{pair}.response.json")

    def run():
        if "patch" in case:
            patch_json(request if case["patch_target"] == "request" else response, deepcopy(case["patch"]))
        if case["id"] == "duplicate_fact_id":
            # Explicitly reach the duplicate-ID guard rather than merely a stale hash.
            request.update(seal_request(request))
            response["context_hash"] = request["context_hash"]
        validate(request)
        validate(response)
        return validate_response(
            request, response, current_state_version=case.get("current_state_version", 1),
            current_ruleset_digest=case.get("current_ruleset_digest", MANIFEST["fixture_ruleset_digest"]),
            consumed_request_ids=case.get("consumed_request_ids", ()),
        )

    if case.get("reject"):
        with pytest.raises(ValueError):
            run()
    else:
        result = run()
        assert result["status"] == case["expected_status"]
        assert result["commit_allowed"] is False
        assert request == load_json(V2 / f"tests/messages/{pair}.request.json")


@pytest.mark.parametrize("case", MANIFEST["schema_cases"], ids=lambda case: case["file"])
def test_schema_vector(case):
    instance = load_json(V2 / case["file"])
    if case["valid"]:
        validate(instance, case["schema"])
    else:
        with pytest.raises(ValueError, match="JSON Schema rejected"):
            validate(instance, case["schema"])
