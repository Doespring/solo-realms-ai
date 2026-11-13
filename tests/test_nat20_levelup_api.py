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


def leveled_campaign(tmp_path, monkeypatch, *, level, experience):
    """A real Nat20 hero at ``level`` whose reward ledger earned ``experience``.

    The hero is created through the same HTTP path the player uses, with a
    subclass where the class gate needs one and a kit in its hands, so the
    advancement has a character that already exists to keep unchanged.
    """
    monkeypatch.setattr("solo.nat20_api.secrets.randbits", lambda bits: 5)
    app = create_app(tmp_path / ("level" + str(level) + ".sqlite3"), PreviewMock(),
                     experimental=True, offline_mock=True)
    build = legal(
        class_slug="fighter", species_slug="human", background="soldier",
        subclass_slug="champion" if level >= 3 else None, level=level,
        equipment=["longsword", "chain-mail", "shield"],
    )
    with TestClient(app) as api:
        created = api.post("/api/prototype/campaigns", json=dict(
            name="Арден", request_id="create-1", adventure="authored",
            description="Пустой погреб.", build=build, prepared_spells=[],
            hero_background="Служил в городской страже.",
            encounter_monsters=["rat"],
            encounter_attack_bonuses={"rat": 0},
            encounter_attack_sources={
                "rat": "Authored smoke encounter: explicit +0 selected by test author."
            },
        ))
        assert created.status_code == 200, created.text
        cid = created.json()["id"]
        current = app.state.store.campaign(cid)
        runtime = current["state"]["nat20"]
        reward = runtime["definition"]["encounters"]["opening"]["reward"]
        runtime["experience"] = experience
        runtime["rewards"] = [dict(
            id=reward["id"], encounter_id="opening", combat_id="opening",
            experience=experience, outcome_sha256="0" * 64, source=reward["source"],
        )]
        with app.state.store.connection(True) as db:
            db.execute(
                "UPDATE campaigns SET state=?, version=version+1 WHERE id=?",
                (json.dumps(current["state"], ensure_ascii=False), cid),
            )
    return app, cid


def test_advancement_offers_only_the_step_the_new_level_opens(tmp_path, monkeypatch):
    """7 -> 8 on a fighter: the eighth level's score increase, nothing else."""
    app, cid = leveled_campaign(tmp_path, monkeypatch, level=7, experience=34000)
    with TestClient(app) as api:
        state = api.get("/api/campaigns/" + cid + "/level-up").json()
    assert state["available"] is True
    assert (state["from_level"], state["to_level"]) == (7, 8)
    creation_only = {"species", "lineage", "class", "background", "scores",
                     "equipment", "final"}
    owed = [
        step for step in state["contract"]["steps"]
        if step["id"] not in creation_only and step.get("required")
        and step.get("status") != "complete"
    ]
    assert [step["kind"] for step in owed] == ["asi"]
    assert owed[0]["level"] == 8
    assert owed[0]["choice_key"].endswith("asi:8:Ability Score Improvement")
    assert state["contract"]["blocking"] == ["skills"]


def test_a_named_jump_is_refused_in_words_and_the_hero_is_untouched(tmp_path, monkeypatch):
    """A client naming level 20 for a 7th-level hero gets words, not a bare 400."""
    app, cid = leveled_campaign(tmp_path, monkeypatch, level=7, experience=34000)
    with TestClient(app) as api:
        state = api.get("/api/campaigns/" + cid + "/level-up").json()
        before = app.state.store.campaign(cid)
        response = api.post("/api/campaigns/" + cid + "/level-up", json=dict(
            request_id="jump-to-20", expected_version=state["version"],
            draft=dict(state["draft"], level=20),
        ))
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert "один уровень за подтверждение" in detail
        assert "20-й" in detail and "8-й" in detail
        after = app.state.store.campaign(cid)
        assert after["state"] == before["state"]
        assert after["version"] == before["version"]


def test_a_confirmed_level_up_keeps_the_same_character(tmp_path, monkeypatch):
    """The eighth level is granted, and the hero stays the hero it was."""
    app, cid = leveled_campaign(tmp_path, monkeypatch, level=7, experience=34000)
    with TestClient(app) as api:
        state = api.get("/api/campaigns/" + cid + "/level-up").json()
        before = app.state.store.campaign(cid)["state"]
        response = api.post("/api/campaigns/" + cid + "/level-up", json=dict(
            request_id="level-8", expected_version=state["version"],
            draft=state["draft"],
            answers={"class:fighter:asi:8:Ability Score Improvement": {"dexterity": 2}},
        ))
        assert response.status_code == 200, response.text
        assert response.json()["to_level"] == 8
        after = app.state.store.campaign(cid)["state"]
    build = after["nat20"]["definition"]["build"]
    assert build["level"] == 8
    assert build["subclass_slug"] == "champion"
    assert build["species_slug"] == before["nat20"]["definition"]["build"]["species_slug"]
    assert after["hero"]["name"] == before["hero"]["name"] == "Арден"
    assert after["hero"]["background"] == before["hero"]["background"]
    assert after["hero"]["background"] == "Служил в городской страже."
    # The kit lives in the native build the level_advance rebuilt from the draft.
    assert build["equipment"] == before["nat20"]["definition"]["build"]["equipment"]
    assert build["equipment"], "the hero must keep the kit it was built with"
    assert after["hero"]["hp"] == before["hero"]["hp"]
    assert after["nat20"]["sheet"]["hp_max"] > before["nat20"]["sheet"]["hp_max"]
