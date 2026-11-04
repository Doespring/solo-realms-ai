"""Reproducible acceptance runner; owns and removes only its temporary DBs."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def hashes(paths):
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if path.is_file()}


def main():
    report = ROOT / "integration/nat20/reports"
    report.mkdir(parents=True, exist_ok=True)
    user_paths = [*list((ROOT / "data").glob("*.sqlite3*")), *list((ROOT / "data").glob("*.json"))]
    before = hashes(user_paths)
    started = time.monotonic()
    temporary = tempfile.TemporaryDirectory(prefix="solo-nat20-acceptance-")
    owned = Path(temporary.name).resolve()
    if owned.parent != Path(tempfile.gettempdir()).resolve():
        raise ValueError("unexpected acceptance directory")
    try:
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m", "pytest", "tests/test_nat20_api.py",
            "-q", "-p", "no:cacheprovider", "--tb=short", "--basetemp", str(owned / "pytest"),
            "--junitxml", str(report / "targeted.junit.xml")], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", timeout=240,
            env=dict(os.environ, SOLO_TEST="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1"))
    finally:
        if Path(temporary.name).resolve() != owned or owned.parent != Path(tempfile.gettempdir()).resolve():
            raise ValueError("refusing unexpected temporary cleanup")
        temporary.cleanup()
    (report / "targeted.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    cases = list(ET.parse(report / "targeted.junit.xml").getroot().iter("testcase"))
    failures = [case.attrib["name"] for case in cases if case.find("failure") is not None or case.find("error") is not None]
    after = hashes(user_paths)
    summary = {"schema_version": "1.0.0", "checked_on": "2026-10-17", "tests": len(cases),
        "passed": len(cases)-len(failures), "failed": failures, "pytest_exit_code": result.returncode,
        "duration_seconds": round(time.monotonic()-started, 2), "user_data_unchanged": before == after,
        "user_data_sha256": after, "temporary_test_root_removed": not owned.exists(),
        "native_runtime": "Nat20 0.7.0 at e187c813bb4e43c4e4e62942ecd83831f1cdbcaa",
        "imported": "12 typed Foundry dnd5e 5.3.3 SRD 5.2 records + one narrow encounter projection",
        "indexed_only": "40 pack manifests, including 9 Foundry 2024 packs; Laaru remains 2014/HB quarantine",
        "restoration": "public Nat20 start + seeded command replay in a new process; compare every event/read-model digest",
        "limits": ["one level-one human fighter / normal-or-disadvantaged goblin Scimitar scenario",
                   "256-command combat replay ceiling; fail closed on divergence/version change",
                   "Foundry JS activities outside the bound scenario, full feats/choice progression and full content still require porting",
                   "Nat20 action intents may auto-end turns; not full Foundry action timing",
                   "trusted Python API only; no UI/LLM routing or activation of existing campaigns"],
        "code_sha256": hashes([*list((ROOT / "solo").glob("nat20_*.py")), ROOT / "tests/test_nat20_api.py",
                               ROOT / "solo/game_commands.py", ROOT / "solo/engine_memory_contracts.py"]),
        "content_sha256": hashes([*list((ROOT / "integration/nat20/imported").rglob("*.json")),
                                  ROOT / "integration/nat20/pack-inventory.json", ROOT / "vendor/nat20/UPSTREAM.json"]),
        "ui_modified_by_this_integration": False, "existing_campaigns_migrated": False,
        "llm_calls": 0, "paid_api_calls": 0, "exe_builds": 0, "foundry_laaru_js_executed": False}
    (report / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({key:summary[key] for key in ("tests", "passed", "failed", "user_data_unchanged", "temporary_test_root_removed")}, ensure_ascii=False))
    if failures:
        print(result.stdout[-7000:])
    return result.returncode or int(before != after)


if __name__ == "__main__":
    raise SystemExit(main())
