"""r247: идентификатор объекта из ключа и карантин, сведённый по причинам.

Большая книга (прогон f433b435, 128 из 128 блоков) собралась, но 671 запись
ушла в карантин, а экран показывал их одной стеной одинаковых строк. Два
дефекта, которые здесь закреплены:

1. Разбор называет объект (`{"name": "Рабские оковы"}`) и не присылает `id`,
   которого требует сцена. Ключ элемента — уже идентификатор с тем же правилом
   написания, поэтому он и подставляется: предмет со страницы попадает в мир,
   а не выбрасывается целиком. Имя по-прежнему обязано быть напечатано.
2. `blocked_operations` остаётся полным списком, но рядом появляется сводка
   `blocked_groups`: вид элемента, причина и число записей. Экран показывает
   причины, а не сотни строк.
"""
import json
import re
import time

from fastapi.testclient import TestClient

from solo.app import create_app
from solo.prototype_prepared import _blocked_groups, prepared_start_readiness
from test_pdf_nat20_start import prepare
from test_preparation_pipeline import PreparationFixture, new, terminal

SOURCE = ("Прочитайте игрокам:\nЗагон.\n\n"
          "Загон. Рабские оковы висят на стене. Персонажи могут заслужить свободу "
          "через службу Каменной Страже. Это приключение пятой редакции.")


def book(payload):
    page = payload["pages"][0]
    span = re.match(r"\[(S\d+)\]", page["text"]).group(1)
    proof = dict(source_span=span)
    return dict(title="Загон", start="Загон", entities=[
        dict(key="pen", kind="location", data=dict(name="Загон"), evidence=[proof]),
        # Разбор назвал предмет, но идентификатора не прислал.
        dict(key="chains", kind="object", location="Загон",
             data=dict(name="Рабские оковы"), evidence=[proof]),
    ])


def prepared(tmp_path):
    llm = PreparationFixture(book)
    client = TestClient(create_app(tmp_path / "solo.sqlite3", llm))
    client.__enter__()
    draft, url = new(client, "Загон, D&D 2014")
    client.post(url + "/files", files={"file": ("pen.txt", SOURCE.encode())}).raise_for_status()
    job = client.get(url).json()["files"][0]["job"]["id"]
    deadline = time.monotonic() + 20
    while client.get("/api/adventure/jobs/" + job).json()["status"] != "complete":
        assert time.monotonic() < deadline
        time.sleep(.03)
    started = client.post(url + "/prepare", json=dict(request_id="pen", consent_to_send=True))
    assert started.status_code == 200, started.text
    status = terminal(client, started.json()["id"])
    return client, draft, status


def test_extracted_object_without_identifier_reaches_the_scene(tmp_path):
    """Объект без `id` от разбора попадает в место под своим ключом."""
    client, draft, status = prepared(tmp_path)
    try:
        assert status["ready"], json.dumps(status, ensure_ascii=False)
        result = json.loads(client.app.state.preparation_pipeline.raw(status["id"])["result"])
        reasons = [q["reason"] for q in result["quarantine"] if q["kind"] == "object"]
        assert not any(reason.startswith("id:") for reason in reasons), reasons
        place = next(p for p in result["definition"]["world"]["locations"] if p["name"] == "Загон")
        objects = [o for o in place["interactions"] if o["name"] == "Рабские оковы"]
        assert objects, place["interactions"]
        assert objects[0]["id"] == "chains", objects[0]
    finally:
        client.__exit__(None, None, None)


def test_blocked_operations_are_grouped_by_reason_not_left_as_a_wall(tmp_path):
    """Сводка карантина называет вид, причину и число, полный список цел."""
    app, client, did, run_id, status, requests = prepare(tmp_path, "optional")
    try:
        readiness = prepared_start_readiness(app.state.preparation, did)
        groups = readiness["blocked_groups"]
        assert groups["total"] == len(readiness["blocked_operations"]), readiness
        assert groups["shown"], groups
        assert groups["shown"][0]["count"] >= 1
        assert any(row["kind"] == "special_rule" and row["label"] == "особых правил"
                   for row in groups["shown"]), groups["shown"]
        assert sum(row["count"] for row in groups["shown"]) <= groups["total"]
        # Экран получает причины, а не строку за строкой.
        assert client.get('/api/creation/drafts/' + did + '/world-readiness').json()["blocked_groups"]["total"] >= 1
    finally:
        client.__exit__(None, None, None)


def test_one_reason_with_a_long_tail_collapses_into_one_group():
    """Имя места в хвосте причины отличает записи, но не причину."""
    rows = [dict(kind="route", reason="Неоднозначный переход не соединён: Ссылка на место по роду неоднозначна: Пещера Кэирнгорм, самая отдалённая, Культивационная пещера"),
            dict(kind="route", reason="Неоднозначный переход не соединён: Ссылка на место по роду неоднозначна: Пещеры звона, Верхние пещеры"),
            dict(kind="object", reason="id: Field required"),
            dict(kind="object", reason="id: Field required")]
    groups = _blocked_groups(rows)
    assert groups["total"] == 4 and groups["groups"] == 2, groups
    assert groups["shown"][0] == dict(kind="object", label="объектов", reason="id: Field required", count=2, detail=""), groups["shown"]
    routes = next(row for row in groups["shown"] if row["kind"] == "route")
    assert routes["count"] == 2 and routes["reason"].startswith("Неоднозначный переход"), routes
    assert groups["hidden"] == 0
