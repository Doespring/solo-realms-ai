"""Level-up through the real journal: atomic, versioned, replay-safe."""
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.preview_mock import PreviewMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402


def campaign(tmp_path, monkeypatch):
    """A real experimental campaign whose opening encounter carries a reward."""
    monkeypatch.setattr("solo.nat20_api.secrets.randbits", lambda bits: 5)
    app = create_app(tmp_path / "game.sqlite3", PreviewMock(), experimental=True, offline_mock=True)
    build = legal(class_slug="fighter", species_slug="human", background="soldier")
    with TestClient(app) as api:
        created = api.post("/api/prototype/campaigns", json=dict(
            name="Арден", request_id="create-1", adventure="authored",
            description="Пустой погреб.", build=build, prepared_spells=[],
            encounter_monsters=["rat"],
            encounter_attack_bonuses={"rat": 0},
            encounter_attack_sources={
                "rat": "Authored smoke encounter: explicit +0 selected by test author."
            },
        ))
        assert created.status_code == 200, created.text
        cid = created.json()["id"]
        # The reward ledger is what grants a level, so the test awards the XP
        # the encounter would have paid, through a registered receipt.
        current = app.state.store.campaign(cid)
        runtime = current["state"]["nat20"]
        reward = runtime["definition"]["encounters"]["opening"]["reward"]
        runtime["experience"] = 300
        runtime["rewards"] = [dict(
            id=reward["id"], encounter_id="opening", combat_id="opening",
            experience=300, outcome_sha256="0" * 64, source=reward["source"],
        )]
        # Written through the same statement the journal uses: the ledger stays
        # the only source of experience, and the receipt is registered in the
        # definition, so DurableState still validates.
        with app.state.store.connection(True) as db:
            db.execute(
                "UPDATE campaigns SET state=?, version=version+1 WHERE id=?",
                (json.dumps(current["state"], ensure_ascii=False), cid),
            )
    return app, cid


def test_an_earned_level_is_offered_with_the_sheet_it_would_produce(tmp_path, monkeypatch):
    app, cid = campaign(tmp_path, monkeypatch)
    with TestClient(app) as api:
        state = api.get("/api/campaigns/" + cid + "/level-up").json()
        assert state["available"] is True
        assert state["from_level"] == 1
        assert state["to_level"] == 2
        assert state["after"]["hp_max"] > state["before"]["hp_max"]
        assert state["contract"]["steps"]


def test_a_confirmed_level_up_commits_once_and_advances_the_definition(tmp_path, monkeypatch):
    app, cid = campaign(tmp_path, monkeypatch)
    with TestClient(app) as api:
        state = api.get("/api/campaigns/" + cid + "/level-up").json()
        draft = state["draft"]
        first = api.post("/api/campaigns/" + cid + "/level-up", json=dict(
            request_id="level-2", expected_version=state["version"], draft=draft,
        ))
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["to_level"] == 2
        committed = app.state.store.campaign(cid)
        build = committed["state"]["nat20"]["definition"]["build"]
        assert build["level"] == 2
        assert committed["state"]["nat20"]["sheet"]["hp_max"] == result["hp_max"]
        again = api.post("/api/campaigns/" + cid + "/level-up", json=dict(
            request_id="level-2", expected_version=state["version"], draft=draft,
        ))
        assert again.status_code == 200, again.text
        assert again.json()["to_level"] == 2
        assert app.state.store.campaign(cid)["state"]["nat20"]["definition"]["build"]["level"] == 2
        # The same id reused for a different command must be refused, not replayed:
        # otherwise one confirmation could authorise a second, unrelated advancement.
        other = api.post("/api/campaigns/" + cid + "/level-up", json=dict(
            request_id="level-2", expected_version=state["version"],
            draft=dict(draft, selected_choices=[]),
        ))
        assert other.status_code != 200
        assert app.state.store.campaign(cid)["state"]["nat20"]["definition"]["build"]["level"] == 2


def test_a_stale_version_cannot_commit_a_level(tmp_path, monkeypatch):
    app, cid = campaign(tmp_path, monkeypatch)
    with TestClient(app) as api:
        state = api.get("/api/campaigns/" + cid + "/level-up").json()
        stale = state["version"] + 5
        response = api.post("/api/campaigns/" + cid + "/level-up", json=dict(
            request_id="level-2", expected_version=stale, draft=state["draft"],
        ))
        assert response.status_code >= 400
        assert app.state.store.campaign(cid)["state"]["nat20"]["definition"]["build"]["level"] == 1