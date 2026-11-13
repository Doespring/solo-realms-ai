"""Wizard contract endpoints: the same gate the screen and direct HTTP use."""
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.preview_mock import PreviewMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nat20_builds import legal  # noqa: E402


def client(tmp_path, monkeypatch):
    monkeypatch.setattr("solo.nat20_api.secrets.randbits", lambda bits: 5)
    app = create_app(tmp_path / "game.sqlite3", PreviewMock(), experimental=True, offline_mock=True)
    return app, TestClient(app)


def fighter():
    build = legal(class_slug="fighter", species_slug="human", background="soldier")
    return dict(
        species_slug="human",
        class_slug="fighter",
        background_slug="soldier",
        ability_score_method="standard_array",
        ability_scores=build["ability_scores"],
        selected_choices=list(build["selected_choices"]),
        equipment_kits={"class:fighter": "A", "background:soldier": "A"},
        name="Арден",
    )


def test_the_contract_route_reports_steps_and_a_sheet(tmp_path, monkeypatch):
    _, api = client(tmp_path, monkeypatch)
    with api:
        body = api.post("/api/prototype/wizard/contract", json=dict(draft=fighter()))
        assert body.status_code == 200, body.text
        state = body.json()
        assert state["ready"] is True
        assert state["sheet"]["hp_max"] > 0
        assert [row["id"] for row in state["steps"]][0] == "species"


def test_an_illegal_draft_is_refused_with_the_native_reason(tmp_path, monkeypatch):
    _, api = client(tmp_path, monkeypatch)
    with api:
        draft = fighter()
        draft["selected_choices"] = []
        state = api.post("/api/prototype/wizard/contract", json=dict(draft=draft)).json()
        assert state["ready"] is False
        assert state["blocking"]
        assert any("advancement" in issue for issue in state["issues"])


def test_an_illegal_subclass_is_refused(tmp_path, monkeypatch):
    _, api = client(tmp_path, monkeypatch)
    with api:
        draft = fighter()
        draft["subclass_slug"] = "champion"
        state = api.post("/api/prototype/wizard/contract", json=dict(draft=draft)).json()
        assert state["ready"] is False
        assert any("subclass" in issue for issue in state["issues"])


def test_an_unknown_field_is_rejected_not_ignored(tmp_path, monkeypatch):
    _, api = client(tmp_path, monkeypatch)
    with api:
        draft = fighter()
        draft["free_scores"] = [18, 18, 18, 18, 18, 18]
        response = api.post("/api/prototype/wizard/contract", json=dict(draft=draft))
        assert response.status_code in (409, 422)


def test_a_level_up_preview_shows_before_and_after(tmp_path, monkeypatch):
    _, api = client(tmp_path, monkeypatch)
    with api:
        draft = fighter()
        draft["level"] = 2
        body = api.post("/api/prototype/wizard/level-up/preview", json=dict(draft=draft))
        assert body.status_code == 200, body.text
        state = body.json()
        assert state["from_level"] == 2
        assert state["to_level"] == 3
        assert state["opened"] == ["class:fighter:subclass:3:Subclass"]
        assert state["after"]["hp_max"] > state["before"]["hp_max"]
        assert state["contract"]["blocking"]


def test_a_campaign_without_earned_progress_offers_no_level_up(tmp_path, monkeypatch):
    app, api = client(tmp_path, monkeypatch)
    build = legal(class_slug="fighter", species_slug="human", background="soldier")
    with api:
        created = api.post("/api/prototype/campaigns", json=dict(
            name="Арден", request_id="create-1", adventure="authored",
            description="Пустой погреб.", build=build, prepared_spells=[],
        ))
        assert created.status_code == 200, created.text
        cid = created.json()["id"]
        state = api.get("/api/campaigns/" + cid + "/level-up").json()
        assert state["available"] is False
        assert "Нет доступного повышения" in state["reason"]