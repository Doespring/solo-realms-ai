"""r169: живой прогон — по одному подклассу книги на каждый класс.

Не тест: печатает то, что реально видит игрок. Кампания создаётся нативным
путём (`create_experimental_campaign`), затем лист читается из
`GET /api/campaigns/{id}` тем же HTTP-клиентом, что и приложение.

    SOLO_TEST=1 .venv/bin/python scripts/probe_r169_subclasses.py
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
from solo.nat20_canonical import PhbSubclassLoader, feature_name, records  # noqa: E402
from solo.nat20_prototype import DATA  # noqa: E402
from solo.nat20_wizard import _sheet_projection  # noqa: E402

DB = ROOT / "runtime" / "r169-probe.sqlite3"


def main() -> int:
    if DB.exists():
        DB.unlink()
    app = create_app(DB)
    loader = PhbSubclassLoader(root=DATA)

    out, failed = {}, []
    by_class: dict[str, list[str]] = {}
    for record in records():
        by_class.setdefault(record.class_identifier, []).append(record.slug)

    for class_slug, slugs in sorted(by_class.items()):
        subclass = slugs[0]
        build = legal(class_slug=class_slug, species_slug="human", level=3,
                      subclass_slug=subclass, equipment=["dagger", "leather-armor"])
        api = Nat20GameEngine(app.state.store)
        campaign = api.create_experimental_campaign(
            "Проба " + subclass, build=build, encounters={}, seed=7,
            title="Проба", description="r169")
        with TestClient(app) as client:
            response = client.get("/api/campaigns/" + campaign["id"])
        if response.status_code != 200:
            failed.append((subclass, response.status_code, response.text[:200]))
            continue
        hero = response.json()["state"]["hero"]
        from dnd5e_engine.build_spec import CharacterBuildSpec, derive_sheet
        sheet = derive_sheet(CharacterBuildSpec.model_validate(build), loader=loader)
        named = {row["id"]: row for row in _sheet_projection(sheet, loader)["features"]}
        own = [ref.slug for ref in loader.get_subclass(subclass).granted_features]
        seen = [row for row in own if row in hero["features"]]
        out[subclass] = dict(
            campaign=campaign["id"],
            class_slug=class_slug,
            level=hero["level"],
            class_name=hero["class_name"],
            granted=own,
            on_sheet=seen,
            player_sees=[dict(id=row, name=named.get(row, {}).get("name", feature_name(row) or row),
                              source=named.get(row, {}).get("source")) for row in seen],
        )
        print(f"### {class_slug:10s} {subclass}: кампания {campaign['id']}, "
              f"{hero['class_name']} {hero['level']} ур., умений на листе "
              f"{len(seen)}/{len(own)}")
        for row in out[subclass]["player_sees"]:
            print("   ", row["name"], "—", row["source"] or "без маркера")

    (ROOT / "reports" / "r169-live.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if failed:
        print("ПРОВАЛ:", failed)
        return 1
    print(f"итого кампаний: {len(out)}; подклассов книги в каталоге: {len(records())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
