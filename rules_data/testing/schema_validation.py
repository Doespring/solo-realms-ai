"""Offline JSON Schema validation for the pinned v2 rule fixtures.

The earlier stand shelled out to ``pwsh`` and PowerShell ``Test-Json``. That
tool is absent from Windows PowerShell 5.1 (which ships without ``Test-Json``),
and ``Test-Json`` also predates ``$defs``: these schemas are draft 2020-12 and
use 47 ``#/$defs/...`` references. Rather than install a second runtime, this
module validates with the reference implementation (``jsonschema``), which
supports draft 2020-12, resolves ``$defs`` and ``$ref`` locally, and never
fetches a schema over the network.
"""
from functools import lru_cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "rules_data" / "v2"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def _local_refs(document):
    """Every ``$ref`` target, so a remote reference fails instead of being fetched."""
    found = []
    stack = [document]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    found.append(value)
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return found


def _offline_registry(schemas):
    """Resolve only the pinned schemas already on disk; never reach the network."""
    registry = Registry()
    for name, document in schemas.items():
        registry = registry.with_resource(
            (V2 / name).resolve().as_uri(), Resource.from_contents(document)
        )
    return registry


@lru_cache(maxsize=128)
def _schemas(schema_path):
    # Pin every v2 schema so a cross-file $ref stays local and deterministic.
    return tuple(
        (path.name, path.read_text(encoding="utf-8"))
        for path in sorted(V2.glob("*.json"))
        if _is_schema(path)
    )


def _is_schema(path):
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return isinstance(document, dict) and ("properties" in document or "$defs" in document)


@lru_cache(maxsize=128)
def _validate(serialized, schema_path, schema_bytes):
    # Schema bytes are part of the cache key, so an edited schema cannot reuse
    # a stale verdict.
    document = json.loads(schema_bytes)
    local = {name: json.loads(text) for name, text in _schemas(schema_path)}
    for reference in _local_refs(document):
        if not reference.startswith("#"):
            target = reference.split("#")[0]
            if not any((V2 / name).resolve().as_uri() == target or name == target
                       for name in local):
                raise NoSuchResource(ref=reference)
    validator = Draft202012Validator(
        document, registry=_offline_registry(local), format_checker=None
    )
    return not list(validator.iter_errors(json.loads(serialized)))


def validate(instance, schema="ai-engine.schema.json"):
    schema_path = (V2 / schema).resolve()
    if schema_path.parent != V2.resolve():
        raise ValueError("schema must be a pinned local v2 schema")
    if not schema_path.is_file():
        raise ValueError("schema must be a pinned local v2 schema")
    serialized = json.dumps(instance, ensure_ascii=False, allow_nan=False)
    if not _validate(serialized, str(schema_path), schema_path.read_text(encoding="utf-8")):
        raise ValueError("JSON Schema rejected instance")


def patch_json(document, patches):
    """Only data operations needed by the existing RFC 6902 test fixtures."""
    for patch in patches:
        if patch["op"] not in ("add", "replace"):
            raise ValueError("unregistered test patch operation")
        parts = [part.replace("~1", "/").replace("~0", "~")
                 for part in patch["path"].split("/")[1:]]
        parent = document
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = parts[-1]
        if isinstance(parent, list):
            if patch["op"] == "add":
                parent.insert(len(parent) if key == "-" else int(key), patch["value"])
            else:
                parent[int(key)] = patch["value"]
        else:
            if patch["op"] == "replace" and key not in parent:
                raise ValueError("test patch target absent")
            parent[key] = patch["value"]
