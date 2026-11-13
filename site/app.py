"""Сайт Solo Realms AI: вход, герой, игра в браузере.

Запуск:  .venv/bin/python site/app.py        (по умолчанию 127.0.0.1:19216)
Порт:    SOLO_SITE_PORT=8080
База:    SOLO_SITE_DB=/path/site.db

Механику считает движок Nat20 (dnd5e_engine) — здесь только хранение,
сессии и проза мастера.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent))

import heroes as H
import narrator
import views
from store import Store

HERE = Path(__file__).parent
COOKIE = "solo_session"
PBKDF2_ROUNDS = 210_000

store = Store()
app = FastAPI(title="Solo Realms AI", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# ── пароли и сессии ─────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    return store.user_by_token(token)


def check_csrf(request: Request) -> bool:
    """SameSite=Lax уже отсекает чужие формы; здесь — запасная проверка Origin."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = request.headers.get("host", "")
    return origin.split("://")[-1] == host


def guard(request: Request):
    """Возвращает (user, None) либо (None, redirect)."""
    user = current_user(request)
    if user is None:
        return None, RedirectResponse("/login", status_code=303)
    return user, None


def page(html: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(html, status_code=status)


# ── страницы входа ──────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    if current_user(request):
        return RedirectResponse("/heroes", status_code=303)
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if current_user(request):
        return RedirectResponse("/heroes", status_code=303)
    return page(views.auth_page(mode="login"))


@app.post("/login")
def login(request: Request, email: str = Form(""), password: str = Form("")):
    if not check_csrf(request):
        return page(views.auth_page(mode="login", error="Запрос отклонён."), 400)
    user = store.user_by_email(email)
    if user is None or not verify_password(password, user["pw_hash"]):
        return page(views.auth_page(mode="login",
                                    error="Нет такого аккаунта или пароль не подошёл.",
                                    values={"email": email}), 401)
    token = secrets.token_urlsafe(32)
    store.open_session(token, user["id"])
    resp = RedirectResponse("/heroes", status_code=303)
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@app.get("/register", response_class=HTMLResponse)
def register_form(request: Request):
    if current_user(request):
        return RedirectResponse("/heroes", status_code=303)
    return page(views.auth_page(mode="register"))


@app.post("/register")
def register(request: Request, email: str = Form(""), name: str = Form(""), password: str = Form("")):
    values = {"email": email, "name": name}
    if not check_csrf(request):
        return page(views.auth_page(mode="register", error="Запрос отклонён.", values=values), 400)
    email = email.strip().lower()
    name = name.strip()
    if "@" not in email or "." not in email.split("@")[-1]:
        return page(views.auth_page(mode="register", error="Нужна настоящая почта — на неё завязан вход.", values=values), 400)
    if len(name) < 2:
        return page(views.auth_page(mode="register", error="Имя слишком короткое.", values=values), 400)
    if len(password) < 8:
        return page(views.auth_page(mode="register", error="Пароль короче восьми символов.", values=values), 400)
    if store.user_by_email(email):
        return page(views.auth_page(mode="register", error="Такая почта уже занята — войдите.", values=values), 409)
    uid = store.create_user(email, name, hash_password(password))
    token = secrets.token_urlsafe(32)
    store.open_session(token, uid)
    resp = RedirectResponse("/heroes", status_code=303)
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return resp


@app.post("/logout")
def logout(request: Request):
    token = request.cookies.get(COOKIE)
    if token:
        store.close_session(token)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    return resp


# ── герои ───────────────────────────────────────────────────────────────────
@app.get("/heroes", response_class=HTMLResponse)
def heroes_list(request: Request):
    user, redirect = guard(request)
    if redirect:
        return redirect
    rows = [H.summary(r) for r in store.heroes(user["id"])]
    return page(views.heroes_page(rows, user))


@app.get("/hero/new", response_class=HTMLResponse)
def hero_new_form(request: Request):
    user, redirect = guard(request)
    if redirect:
        return redirect
    return page(views.new_hero_page(user=user))


@app.post("/hero/new", response_class=HTMLResponse)
def hero_new(request: Request, name: str = Form(""), species: str = Form("human"),
             klass: str = Form("fighter"), background: str = Form("soldier"),
             ab_strength: int = Form(10), ab_dexterity: int = Form(10),
             ab_constitution: int = Form(10), ab_intelligence: int = Form(10),
             ab_wisdom: int = Form(10), ab_charisma: int = Form(10)):
    user, redirect = guard(request)
    if redirect:
        return redirect

    values = {"name": name, "species": species, "klass": klass, "background": background,
              "ab_strength": ab_strength, "ab_dexterity": ab_dexterity,
              "ab_constitution": ab_constitution, "ab_intelligence": ab_intelligence,
              "ab_wisdom": ab_wisdom, "ab_charisma": ab_charisma}
    abilities = {"strength": ab_strength, "dexterity": ab_dexterity,
                 "constitution": ab_constitution, "intelligence": ab_intelligence,
                 "wisdom": ab_wisdom, "charisma": ab_charisma}

    name = name.strip()
    if not 2 <= len(name) <= 40:
        return page(views.new_hero_page("Имя героя — от 2 до 40 знаков.", values, user), 400)
    if species not in H.SPECIES or klass not in H.CLASSES or background not in H.BACKGROUNDS:
        return page(views.new_hero_page("Неизвестный вид, класс или предыстория.", values, user), 400)
    if any(not H.ABILITY_MIN <= v <= H.ABILITY_MAX for v in abilities.values()):
        return page(views.new_hero_page("Характеристика вне диапазона 8–15.", values, user), 400)
    if H.points_used(abilities) > H.ABILITY_POINTS:
        return page(views.new_hero_page(
            f"Потрачено {H.points_used(abilities)} очков из {H.ABILITY_POINTS}.", values, user), 400)

    sheet = H.build_sheet(species, klass, background, 1, abilities, name)
    store.add_hero(user["id"], name, species, klass, background, 1, abilities, sheet)
    return RedirectResponse("/heroes", status_code=303)


@app.get("/hero/{hero_id}", response_class=HTMLResponse)
def hero_card(request: Request, hero_id: int):
    user, redirect = guard(request)
    if redirect:
        return redirect
    row = store.hero(hero_id)
    if row is None or row["user_id"] != user["id"]:
        return page(views.not_found("Герой не найден", user), 404)
    return page(views.hero_page(row, H.abilities_json(row), H.sheet_json(row), user))


@app.post("/hero/{hero_id}/delete")
def hero_delete(request: Request, hero_id: int):
    user, redirect = guard(request)
    if redirect:
        return redirect
    row = store.hero(hero_id)
    if row and row["user_id"] == user["id"]:
        store.delete_hero(hero_id)
    return RedirectResponse("/heroes", status_code=303)


# ── приключения ─────────────────────────────────────────────────────────────
@app.get("/campaigns", response_class=HTMLResponse)
def campaigns_list(request: Request):
    user, redirect = guard(request)
    if redirect:
        return redirect
    return page(views.campaigns_page(store.campaigns(user["id"]), user))


@app.post("/campaign/start")
def campaign_start(request: Request, hero_id: int = Form(...)):
    user, redirect = guard(request)
    if redirect:
        return redirect
    row = store.hero(hero_id)
    if row is None or row["user_id"] != user["id"]:
        return page(views.not_found("Герой не найден", user), 404)

    summary = H.summary(row)
    title, scene_text = narrator.opening(summary, seed=secrets.randbelow(10**6))
    cid = store.add_campaign(user["id"], hero_id, title, scene_text[:120])
    store.add_message(cid, "master", scene_text)
    return RedirectResponse(f"/play/{cid}", status_code=303)


@app.post("/campaign/{cid}/delete")
def campaign_delete(request: Request, cid: int):
    user, redirect = guard(request)
    if redirect:
        return redirect
    row = store.campaign(cid)
    if row and row["user_id"] == user["id"]:
        store.delete_campaign(cid)
    return RedirectResponse("/campaigns", status_code=303)


# ── игра ────────────────────────────────────────────────────────────────────
@app.get("/play/{cid}", response_class=HTMLResponse)
def play(request: Request, cid: int):
    user, redirect = guard(request)
    if redirect:
        return redirect
    row = store.campaign(cid)
    if row is None or row["user_id"] != user["id"]:
        return page(views.not_found("Приключение не найдено", user), 404)
    return page(views.play_page(row, store.messages(cid), H.sheet_json_row(row), H.abilities_json_row(row)))


@app.post("/play/{cid}/act", response_class=HTMLResponse)
def act(request: Request, cid: int, action: str = Form("")):
    user, redirect = guard(request)
    if redirect:
        return redirect
    row = store.campaign(cid)
    if row is None or row["user_id"] != user["id"]:
        return page(views.not_found("Приключение не найдено", user), 404)

    action = " ".join(action.split())[:600]
    if not action:
        return RedirectResponse(f"/play/{cid}", status_code=303)

    sheet = H.sheet_json_row(row)
    abilities = H.abilities_json_row(row)
    hero = H.summary_row(row)

    store.add_message(cid, "player", action)
    result = narrator.narrate(action, hero, sheet, abilities, row["turn"],
                              seed=secrets.randbelow(10**6))

    if result.get("check"):
        check = result["check"]
        verdict = "успех" if check["success"] else "провал"
        store.add_message(cid, "roll",
                          f"{check['label'] or 'Проверка'}: d20 {check['natural']} "
                          f"{check['modifier']:+d} = {check['total']} против Сл {check['dc']} — {verdict}",
                          {"check": check})
    store.add_message(cid, "master", result["text"], {"roll": result.get("roll")})

    if result.get("damage"):
        sheet["hp_current"] = max(0, int(sheet.get("hp_current", sheet.get("hp_max", 0)))
                                  - int(result["damage"]))
        store.save_sheet(row["hero_id"], sheet)
    store.touch_campaign(cid, row["scene"], int(row["turn"]) + 1)

    updated = store.campaign(cid)
    return page(views.play_page(updated, store.messages(cid), H.sheet_json_row(updated), H.abilities_json_row(updated)))


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("SOLO_SITE_PORT", "19216"))
    host = os.environ.get("SOLO_SITE_HOST", "127.0.0.1")
    print(f"Сайт: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
