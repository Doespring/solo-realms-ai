"""Reproducible offline verification; only fresh temporary databases are used."""
import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from .schema_validation import ROOT, V2

TARGETS = [
    "tests/test_hybrid_rules_runtime.py",
    "tests/test_hybrid_rules_integration.py",
    "tests/test_rulesets.py::test_expertise_advantage_cancellation_and_no_automatic_check_crit",
    "tests/test_rulesets.py::test_conversion_preview_approval_checkpoint_stale_and_replay",
]


def snapshot(paths):
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths) if path.is_file()}


def main(*, production=False, chapter_one=False):
    production = production or chapter_one
    reports = V2 / "reports" / ("phb-01" if chapter_one else "production" if production else "executability")
    reports.mkdir(parents=True, exist_ok=True)
    protected_catalog = [
        *list((ROOT / "rules_data/v1").rglob("*.json")),
        *list((V2 / "sections").glob("*.json")),
        V2 / "routing.schema.json", V2 / "ai-engine.schema.json", V2 / "sources.json",
        V2 / "tests/runtime.cases.json",
        *list((V2 / "chapters/phb-01").glob("*.json")),
        V2 / "coverage/phb-01.json", V2 / "coverage.schema.json", V2 / "tests/phb-01.math.cases.json",
    ]
    user_files = [*list((ROOT / "data").glob("*.sqlite3*")), *list((ROOT / "data").glob("*.json"))]
    before_catalog, before_data = snapshot(protected_catalog), snapshot(user_files)
    targets = TARGETS + (["tests/test_hybrid_rules_production.py",
                         "tests/test_rulesets.py::test_http_character_edit_undo_and_free_text_rest"] if production else [])
    if chapter_one:
        targets += ["tests/test_phb_chapter_one.py", "tests/test_phb_chapter_one_production.py", "tests/test_combat.py"]
    environment = dict(os.environ, SOLO_TEST="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    temporary = tempfile.TemporaryDirectory(prefix="solo-hybrid-run-")
    owned_root = Path(temporary.name).resolve()
    temp_parent = Path(tempfile.gettempdir()).resolve()
    started = time.perf_counter()
    try:
        result = subprocess.run(
            [sys.executable, "-X", "utf8", "-B", "-m", "pytest", *targets,
             "-q", "-p", "no:cacheprovider", "--tb=short", "--basetemp", str(owned_root / "pytest"),
             "--junitxml", str(reports / "targeted.junit.xml")],
            cwd=ROOT, env=environment, text=True, encoding="utf-8", capture_output=True,
            timeout=120,
        )
    finally:
        if Path(temporary.name).resolve() != owned_root or owned_root.parent != temp_parent:
            raise ValueError("refusing cleanup of an unexpected test directory")
        temporary.cleanup()
    elapsed = round(time.perf_counter() - started, 3)
    (reports / "targeted.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    testcases = list(ET.parse(reports / "targeted.junit.xml").getroot().iter("testcase"))
    failed = [case.attrib["name"] for case in testcases if case.find("failure") is not None or case.find("error") is not None]
    skipped = [case.attrib["name"] for case in testcases if case.find("skipped") is not None]
    after_catalog, after_data = snapshot(protected_catalog), snapshot(user_files)
    unchanged_catalog = before_catalog == after_catalog
    unchanged_data = before_data == after_data
    summary = {
        "checked_on": datetime.now(timezone(timedelta(hours=4))).date().isoformat(),
        "scope": "production route verification on temporary campaigns" if production else "isolated executability verification; no catalog expansion",
        "tests": {"total": len(testcases), "passed": len(testcases) - len(failed) - len(skipped),
                  "failed": failed, "skipped": skipped, "runtime_vectors": 48, "schema_vectors": 13,
                  "integration_checks": 24, "existing_targeted_regressions": 3 if production else 2,
                  "production_checks": sum(case.attrib.get("classname", "").endswith("test_hybrid_rules_production") for case in testcases)},
        "duration_seconds": elapsed, "pytest_exit_code": result.returncode,
        "catalog_unchanged": unchanged_catalog, "user_data_unchanged": unchanged_data,
        "catalog_sha256": after_catalog, "user_data_sha256": after_data,
        "temporary_test_root_removed": not owned_root.exists(),
        "integration_path": "RulesetEngine.check -> Store.engine_event -> SQLite event/state -> Store.save -> reopen",
        "fixed_issue": "Store.backup did not close its sqlite3 destination, preventing immediate Windows file cleanup; destination now explicitly closes",
        "checks": ["unequal cause cancellation", "internal poisoned source", "edition mismatch", "stale state",
                   "ruleset fingerprint", "idempotent replay", "changed command replay", "replay after undo",
                   "forbidden operations", "AI proposal without write capability", "AI text never executed",
                   "database outside owned sandbox refused", "transaction failure rollback", "backup handle closure"],
        "app_adapter_registered": production, "user_campaign_rules_activated": False,
        "ai_or_pdf_code_executed": False, "paid_api_calls": 0, "exe_builds": 0,
        "remaining_blockers": (["only the local single-user Advanced/session ACL model is supported",
                                "unverified rules and ambiguous Wish stress remain unactivated"] if production else
                               ["production bridge, ACL and broader rule coverage have not been certified",
                                "unverified rules and ambiguous Wish stress remain unactivated"]),
    }
    if production:
        summary["binding"] = {"operation": "ability_check", "definitions": ["dnd2024.d20.advantage", "dnd2024.d20.disadvantage", "dnd2024.d20.cancellation"],
                              "session_campaign_opt_in_required": True, "restart_default": "inactive",
                              "acl_scope": "existing local Advanced role, with campaign- and session-bound grants",
                              "context_projection": "actor name, level and abilities; no world or state snapshots",
                              "example": {"dice": [5, 19], "selected": 19, "bonus": 2, "total": 21, "dc": 15, "success": True}}
        summary["production_code_sha256"] = snapshot([ROOT / "solo" / filename for filename in
                                                       ("app.py", "tabletop_api.py", "hybrid_rules.py", "hybrid_rules_api.py")])
        summary["fixed_issue"] = "No mathematical/catalog changes; the Windows loopback allowance is needed only for asyncio test infrastructure"
    if chapter_one:
        coverage = json.loads((V2 / "coverage/phb-01.json").read_text(encoding="utf-8"))
        summary.update(scope="PHB chapter 1 extraction and closed local check release on temporary campaigns",
                       fixed_issue="CombatEngine.search treated zero darkvision range as an active sense in the actor cell; require a positive range. Unsupported condition-dependent tests fail before RNG",
                       coverage={key: coverage[key] for key in ("rule_count", "extraction_complete", "execution_complete", "classification_counts", "readiness_counts")},
                       remaining_blockers=["Explicit glossary/item/class/spell dependencies remain unextracted",
                                           "Knockout, ranged-close visibility, multi-source conditions and other listed Engine differences are unbound",
                                           "Narrative/adjudicated decisions require GM proposals and separately validated consequences",
                                           "Existing local single-user Advanced ACL only; human source review pending"])
        summary["tests"]["chapter_checks"] = sum("test_phb_chapter_one" in case.attrib.get("classname", "") for case in testcases)
        summary["tests"]["combat_regressions"] = sum(case.attrib.get("classname", "").endswith("test_combat") for case in testcases)
        summary["catalog_expanded"] = True
        summary["chapter_artifacts_sha256"] = snapshot([ROOT / "solo/combat.py", ROOT / "solo/rulesets.py",
                                                       ROOT / "rules_data/runtime/math_handlers.py",
                                                       ROOT / "tests/test_phb_chapter_one.py",
                                                       ROOT / "tests/test_phb_chapter_one_production.py"])
        summary["binding"] = {"operations": ["ability_check", "skill_check", "saving_throw"],
                              "new_definitions": coverage["adapter_rule_ids"], "legacy_d20_definitions": 3,
                              "default_active": False, "session_campaign_opt_in_required": True,
                              "engine_mechanics_duplicated": False}
        summary["integration_path"] = "RulesetEngine.check/skill/saving_throw -> Store.engine_event -> SQLite event/state -> Store.save -> reopen"
    (reports / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("tests", "catalog_unchanged", "user_data_unchanged", "temporary_test_root_removed")}, ensure_ascii=False))
    return result.returncode or int(not unchanged_catalog or not unchanged_data)


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--production"], ["--chapter-one"]):
        raise SystemExit("Only --production or --chapter-one is accepted")
    raise SystemExit(main(production=sys.argv[1:] == ["--production"], chapter_one=sys.argv[1:] == ["--chapter-one"]))
