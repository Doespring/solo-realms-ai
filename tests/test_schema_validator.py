"""Guard: the new validator must still reject bad input, not pass everything.

Модуль ``rules_data.testing.schema_validation`` живёт в соседнем приложении, а
не в поставке движка: там лежит схема и сам валидатор. Если рядом лежит
приложение с этим модулем, проверка идёт в полную силу; если нет — весь файл
честно пропускается с причиной, а не падает на сборе и не притворяется
зелёным.
"""
import json
import socket
import sys
from pathlib import Path

import pytest

SIBLING_APP = Path(__file__).resolve().parents[2] / "SoloRealmsAI-app"
_schema_validation = SIBLING_APP / "rules_data" / "testing" / "schema_validation.py"

if _schema_validation.is_file():
    # Модуль приложения оживает только вместе со своим корнем в sys.path,
    # иначе импорт разрешается относительно папки движка и не находит схему.
    for entry in (str(SIBLING_APP),):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    from rules_data.testing.schema_validation import V2, load_json, validate
else:
    pytest.skip(
        "проверка по схемам корпуса требует модуль хоста "
        "(rules_data/testing/schema_validation.py) и вместе с ним схему; "
        "в поставке движка ни того, ни другого нет — см. KNOWN_GAPS",
        allow_module_level=True,
    )


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
