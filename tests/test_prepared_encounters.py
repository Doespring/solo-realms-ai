"""An extracted opponent and reward reach the game through the shared path."""
import json, re, time
from fastapi.testclient import TestClient
from solo.app import create_app
from test_preparation_pipeline import PreparationFixture, new, terminal


def book(payload):
    page = payload["pages"][0]
    span = re.match(r"\[(S\d+)\]", page["text"]).group(1)
    proof = dict(source_span=span)
    return dict(title="Соляные копи", start="Вход в копи", entities=[
        dict(key="pit", kind="location", data=dict(name="Вход в копи"), evidence=[proof]),
        dict(key="shaft", kind="location", data=dict(name="Затопленный ствол"), evidence=[proof]),
        dict(key="road", kind="route",
             data={"from": "Вход в копи", "to": "Затопленный ствол",
                   "meters": 200, "minutes": 10, "description": "Спуск"}, evidence=[proof]),
        dict(key="foreman", kind="npc", location="Вход в копи",
             data=dict(name="Мастер Тарн", character="Держит лампу."), evidence=[proof]),
        dict(key="kobolds", kind="opponent", location="Затопленный ствол",
             data=dict(name="кобольд", count=3), evidence=[proof]),
        dict(key="salt-pay", kind="reward", data=dict(name="Плата за соль", experience=50),
             evidence=[proof]),
    ])


def test_extracted_opponent_and_reward_become_executable(tmp_path):
    llm = PreparationFixture(book)
    with TestClient(create_app(tmp_path / "solo.sqlite3", llm)) as client:
        draft, url = new(client, "Соляные копи, D&D 2014")
        # Every extracted number and name is inside the cited span, so the
        # grounded() check proves them instead of guessing.
        source = ("Прочитайте игрокам:\nВход в копи.\n\n"
                  "Вход в копи. Затопленный ствол в 200 метрах, 10 минут спуска. "
                  "Мастер Тарн держит лампу. Внизу 3 кобольда. "
                  "Награда: Плата за соль, 50 опыта. "
                  "Это приключение пятой редакции.")
        client.post(url + "/files", files={"file": ("salt.txt", source.encode())}).raise_for_status()
        job = client.get(url).json()["files"][0]["job"]["id"]
        deadline = time.monotonic() + 20
        while client.get("/api/adventure/jobs/" + job).json()["status"] != "complete":
            assert time.monotonic() < deadline
            time.sleep(.03)
        started = client.post(url + "/prepare", json=dict(request_id="salt", consent_to_send=True))
        assert started.status_code == 200, started.text
        status = terminal(client, started.json()["id"])
        detail = json.loads(client.app.state.preparation_pipeline.raw(status["id"])["result"])
        assert status["ready"], json.dumps(detail["quarantine"], ensure_ascii=False)
        definition = detail["definition"]
        encounters = [e for e in definition["world"]["events"] if e["kind"] == "encounter"]
        assert encounters, definition["world"]["events"]
        assert encounters[0]["location"] == "Затопленный ствол"
        opponent = next(v for v in detail["provenance"].values() if v.get("kind") == "opponent")
        assert opponent["target"] == "kobold-warrior"
        progression = definition["progression"]
        assert progression["mode"] == "experience"
        assert progression["rewards"][0]["amount"] == 50

