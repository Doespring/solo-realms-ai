"""r147: живой прогон — бард 3 уровня каждой коллегии на экране Nat20.

Не тест: печатает то, что реально видит игрок. Кампания создаётся нативным
путём (`create_experimental_campaign`), затем лист читается из
`GET /api/campaigns/{id}` тем же HTTP-клиентом, что и приложение.

    SOLO_TEST=1 .venv/bin/python scripts/probe_r147_subclasses.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from nat20_builds import legal  # noqa: E402
from solo.app import create_app  # noqa: E402
from solo.nat20_api import Nat20GameEngine  # noqa: E402
from solo.nat20_canonical import feature_name  # noqa: E402
from solo.nat20_prototype import DATA  # noqa: E402
from solo.nat20_wizard import _sheet_projection  # noqa: E402
from solo.nat20_canonical import PhbSubclassLoader  # noqa: E402

COLLEGES = ("lore", "phb2024.bard.valor", "phb2024.bard.glamour", "phb2024.bard.dance")
DB = ROOT / "runtime" / "r147-probe.sqlite3"


def main() -> int:
    if DB.exists():
        DB.unlink()
    app = create_app(DB)
    loader = PhbSubclassLoader(root=DATA)
    out = {}
    for slug in COLLEGES:
        build = legal(class_slug="bard", species_slug="human", level=3,
                      subclass_slug=slug, equipment=["dagger", "leather-armor"])
        api = Nat20GameEngine(app.state.store)
        campaign = api.create_experimental_campaign(
            "Проба " + slug, build=build, encounters={}, seed=7,
            title="Проба", description="r147")
        with TestClient(app) as client:
            response = client.get("/api/campaigns/" + campaign["id"])
        assert response.status_code == 200, (slug, response.status_code, response.text[:200])
        hero = response.json()["state"]["hero"]
        from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
        sheet = derive_sheet(CharacterBuildSpec.model_validate(build), loader=loader)
        named = {row["id"]: row for row in _sheet_projection(sheet, loader)["features"]}
        subclass_features = [f for f in hero["features"] if f.startswith("phb2024.") or f in (
            "cutting-words", "bonus-proficiencies", "magical-discoveries", "peerless-skill")]
        out[slug] = dict(
            campaign=campaign["id"],
            level=hero["level"],
            class_name=hero["class_name"],
            features=subclass_features,
            player_sees=[dict(id=row, name=named.get(row, {}).get("name", feature_name(row) or row),
                              source=named.get(row, {}).get("source")) for row in subclass_features],
        )
        print("###", slug, "| кампания", campaign["id"], "| бард", hero["level"])
        for row in out[slug]["player_sees"]:
            print("   ", row["name"], "—", row["source"] or "SRD (пакет)")
    (ROOT / "reports" / "r147-live.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
