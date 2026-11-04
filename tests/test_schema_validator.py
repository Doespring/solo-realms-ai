"""Guard: the new validator must still reject bad input, not pass everything."""
import json
import socket
from pathlib import Path

import pytest

from rules_data.testing.schema_validation import V2, load_json, validate


def test_rejects_wrong_type_and_missing_required():
    with pytest.raises(ValueError, match="JSON Schema rejected"):
        validate({"schema_version": "2.1.0", "rules": [{"id": 5}]}, "routing.schema.json")
    with pytest.raises(ValueError, match="JSON Schema rejected"):
        validate({}, "routing.schema.json")


def test_accepts_the_pinned_fixture():
    validate(load_json(V2 / "tests/messages/dmg.request.json"))


def test_rejects_schema_outside_v2():
    with pytest.raises(ValueError, match="pinned local v2 schema"):
        validate({}, "../schema_validation.py")
    with pytest.raises(ValueError, match="pinned local v2 schema"):
        validate({}, "does-not-exist.schema.json")


def test_never_fetches_a_remote_schema(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("the validator must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    document = json.loads((V2 / "coverage.schema.json").read_text(encoding="utf-8"))
    document = dict(document, properties={"x": {"$ref": "https://example.invalid/remote.json"}})
    (V2 / ".probe-remote.schema.json").write_text(json.dumps(document), encoding="utf-8")
    try:
        with pytest.raises(Exception) as error:
            validate({"x": 1}, ".probe-remote.schema.json")
        assert "remote" in str(error.value).lower() or "unresolv" in str(error.value).lower()
    finally:
        (V2 / ".probe-remote.schema.json").unlink(missing_ok=True)


def test_local_defs_are_resolved_not_skipped():
    # A document valid only because #/$defs was actually followed.
    instance = load_json(V2 / "tests/messages/dmg.request.json")
    validate(instance, "ai-engine.schema.json")
    broken = json.loads(json.dumps(instance))
    broken["request_id"] = 42
    with pytest.raises(ValueError, match="JSON Schema rejected"):
        validate(broken, "ai-engine.schema.json")
