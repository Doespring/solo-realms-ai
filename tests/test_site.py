"""Тесты сайта: хранилище, лист героя, мастер, весь путь игрока через HTTP."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SITE = Path(__file__).resolve().parents[1] / "site"
sys.path.insert(0, str(SITE))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLO_SITE_DB", str(tmp_path / "test.db"))
    import importlib

    import store

    importlib.reload(store)
    import app as app_module

    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_points_used():
    import heroes as H

    flat = {k: 10 for k in H.ABILITIES}          # 6 × 2 = 12
    assert H.points_used(flat) == 12
    assert H.points_used({**flat, "strength": 15}) == 12 - 2 + 9
    assert H.modifier(15) == 2 and H.modifier(8) == -1 and H.modifier(10) == 0


def test_sheet_uses_engine():
    import heroes as H

    sheet = H.build_sheet("dwarf", "fighter", "soldier", 1,
                          {"strength": 15, "dexterity": 13, "constitution": 14,
                           "intelligence": 8, "wisdom": 12, "charisma": 10}, "Тест")
    # числа приходят из dnd5e_engine, а не считаются сайтом
    assert sheet["hp_max"] >= 10
    assert sheet["ac"] >= 10
    assert sheet["proficiency_bonus"] == 2


@pytest.mark.parametrize("phrase,skill", [
    ("Убеждаю хозяина помочь", "persuasion"),
    ("Крадусь по коридору", "stealth"),
    ("Бью плечом в дверь", "athletics"),
    ("Запугиваю стражника", "intimidation"),
    ("Обыскиваю лодку", "investigation"),
    ("Прислушиваюсь у двери", "perception"),
    ("Лечу рану товарища", "medicine"),
])
def test_master_detects_check(phrase, skill):
    import narrator

    got, dc = narrator.detect(phrase)
    assert got == skill and dc > 0


def test_master_narrates_turn():
    import heroes as H
    import narrator

    ab = {"strength": 15, "dexterity": 13, "constitution": 14,
          "intelligence": 8, "wisdom": 12, "charisma": 10}
    sheet = H.build_sheet("human", "fighter", "soldier", 1, ab, "Тест")
    res = narrator.narrate("Бью плечом в дверь", {"name": "Тест", "klass": "Воин"},
                           sheet, ab, 1, seed=5)
    assert res["check"]["dc"] == 14
    assert 1 <= res["check"]["natural"] <= 20
    assert res["text"]
    assert res["check"]["total"] == res["check"]["natural"] + res["check"]["modifier"]


def test_full_player_path(client):
    # регистрация
    r = client.post("/register", data={"email": "a@b.ru", "name": "Иван", "password": "parol12345"},
                    follow_redirects=False)
    assert r.status_code == 303

    # герой
    r = client.post("/hero/new", data={
        "name": "Ирма", "species": "human", "klass": "ranger", "background": "soldier",
        "ab_strength": 15, "ab_dexterity": 13, "ab_constitution": 14,
        "ab_intelligence": 8, "ab_wisdom": 12, "ab_charisma": 10,
    }, follow_redirects=False)
    assert r.status_code == 303
    assert "Ирма" in client.get("/heroes").text

    # старт приключения
    r = client.post("/campaign/start", data={"hero_id": 1}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/play/")

    # ход
    page = client.get(r.headers["location"]).text
    assert "Что делаете?" in page
    page = client.post("/play/1/act", data={"action": "Бью плечом в дверь"}).text
    assert "d20" in page and "против Сл" in page


def test_bad_scores_rejected(client):
    client.post("/register", data={"email": "c@d.ru", "name": "Иван", "password": "parol12345"})
    r = client.post("/hero/new", data={
        "name": "Х", "species": "human", "klass": "fighter", "background": "soldier",
        "ab_strength": 15, "ab_dexterity": 15, "ab_constitution": 15,
        "ab_intelligence": 15, "ab_wisdom": 15, "ab_charisma": 15,
    })
    assert r.status_code == 400
    assert "27" in r.text


def test_login_required(client):
    r = client.get("/heroes", follow_redirects=False)
    assert r.status_code in (303, 307) and r.headers["location"] == "/login"


def test_wrong_password(client):
    client.post("/register", data={"email": "e@f.ru", "name": "Иван", "password": "parol12345"})
    client.post("/logout")
    r = client.post("/login", data={"email": "e@f.ru", "password": "неверный"})
    assert r.status_code == 401
