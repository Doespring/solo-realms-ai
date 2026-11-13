"""HTML сайта. Один шаблон-каркас и страницы; разметка задаётся здесь целиком.

Никаких шаблонизаторов: страницы собираются функциями, чтобы сайт запускался
на голом Python из .venv проекта.
"""
from __future__ import annotations

import html

import heroes as H

SAVE_KEY = {"str": "strength", "dex": "dexterity", "con": "constitution",
            "int": "intelligence", "wis": "wisdom", "cha": "charisma"}


def e(value) -> str:
    return html.escape(str(value), quote=True)


def layout(title: str, body: str, *, user=None, wide: bool = False, bare: bool = False) -> str:
    nav = ""
    if user:
        nav = f"""
        <nav class="topnav">
          <a href="/heroes">Герои</a>
          <a href="/campaigns">Приключения</a>
          <a href="/hero/new">Новый герой</a>
          <form method="post" action="/logout" class="inline"><button class="link" type="submit">Выйти</button></form>
        </nav>"""
    shell = body if bare else f"""
      <header class="topbar">
        <div class="wrap bar">
          <a class="brand" href="/"><b>SOLO REALMS</b><span>AI</span></a>
          {nav}
        </div>
      </header>
      <main class="wrap main">{body}</main>
      <footer class="wrap foot">
        <span>Прототип. Правила считает Nat20, историю рассказывает мастер.</span>
      </footer>"""
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#14110D">
<title>{e(title)} · Solo Realms AI</title>
<link rel="stylesheet" href="/static/site.css">
</head>
<body>
{shell}
</body>
</html>"""


def field(name: str, label: str, *, type_: str = "text", value: str = "",
          required: bool = True, autocomplete: str = "", hint: str = "") -> str:
    req = "required" if required else ""
    hint_html = f'<small>{e(hint)}</small>' if hint else ""
    return f"""<label class="field">
      <span>{e(label)}</span>
      <input type="{type_}" name="{e(name)}" value="{e(value)}" {req}
             autocomplete="{e(autocomplete)}" spellcheck="false">
      {hint_html}
    </label>"""


# ── вход и регистрация ───────────────────────────────────────────────────────
def auth_page(*, mode: str, error: str = "", values: dict | None = None) -> str:
    v = values or {}
    if mode == "register":
        title, action, switching = "Завести аккаунт", "/register", (
            'Уже есть аккаунт — <a href="/login">войти</a>.')
        extra = field("name", "Как вас зовут", value=v.get("name", ""),
                      autocomplete="name", hint="Так мастер будет к вам обращаться.")
    else:
        title, action, switching = "Вход", "/login", (
            'Впервые здесь — <a href="/register">завести аккаунт</a>.')
        extra = ""

    err = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    body = f"""
    <section class="auth">
      <div class="auth-card">
        <h1>{title}</h1>
        <p class="muted">Один игрок, один мастер. История хранится у вас.</p>
        {err}
        <form method="post" action="{action}" class="stack">
          {field("email", "Почта", type_="email", value=v.get("email", ""), autocomplete="email")}
          {extra}
          {field("password", "Пароль", type_="password", autocomplete="current-password" if mode == "login" else "new-password",
                 hint="" if mode == "register" else "")}
          <button class="btn primary" type="submit">{title}</button>
        </form>
        <p class="switch">{switching}</p>
      </div>
    </section>"""
    return layout(title, body, bare=True)


# ── герои ────────────────────────────────────────────────────────────────────

def heroes_page(rows: list[dict], user=None) -> str:
    cards = "".join(
        f"""
        <article class="card hero-card">
          <div class="hero-top">
            <h2>{e(h['name'])}</h2>
            <span class="tag">уровень {h['level']}</span>
          </div>
          <p class="muted">{e(h['species'])} · {e(h['klass'])} · {e(h['background'])}</p>
          <dl class="stats">
            <div><dt>Хиты</dt><dd>{h['hp']}</dd></div>
            <div><dt>Класс брони</dt><dd>{h['ac']}</dd></div>
          </dl>
          <div class="row">
            <form method="post" action="/campaign/start" class="inline">
              <input type="hidden" name="hero_id" value="{h['id']}">
              <button class="btn primary" type="submit">Играть</button>
            </form>
            <form method="post" action="/hero/{h['id']}/delete" class="inline"
                  onsubmit="return confirm('Удалить героя {e(h['name'])}? Приключения с ним тоже исчезнут.')">
              <button class="btn ghost" type="submit">Удалить</button>
            </form>
          </div>
        </article>""" for h in rows)

    empty = """
      <div class="empty">
        <h2>Героев пока нет</h2>
        <p class="muted">Соберите первого — это минута: вид, класс, предыстория и шесть характеристик.</p>
        <a class="btn primary" href="/hero/new">Создать героя</a>
      </div>"""

    body = f"""
    <div class="page-head">
      <h1>Мои герои</h1>
      <a class="btn primary" href="/hero/new">Новый герой</a>
    </div>
    <div class="grid">{cards or empty}</div>"""
    return layout("Герои", body, user=user)


def ability_row(key: str, score: int) -> str:
    return f"""
      <div class="ab-row" data-key="{e(key)}">
        <span class="ab-name">{e(H.ABILITIES[key])}</span>
        <button type="button" class="stepper" data-step="-1" aria-label="Меньше">−</button>
        <output class="ab-val" data-value>{score}</output>
        <button type="button" class="stepper" data-step="1" aria-label="Больше">+</button>
        <span class="ab-mod" data-mod>{score:+d}</span>
        <input type="hidden" name="ab_{e(key)}" value="{score}">
      </div>"""


def new_hero_page(error: str = "", values: dict | None = None, user=None) -> str:
    v = values or {}
    base = {"strength": 10, "dexterity": 10, "constitution": 10,
            "intelligence": 10, "wisdom": 10, "charisma": 10}
    for k in base:
        if v.get(f"ab_{k}"):
            base[k] = int(v[f"ab_{k}"])

    species = "".join(
        f'<option value="{e(k)}"{" selected" if v.get("species") == k else ""}>{e(n)}</option>'
        for k, n in H.SPECIES.items())
    classes = "".join(
        f'<option value="{e(k)}"{" selected" if v.get("klass") == k else ""}>{e(n)}</option>'
        for k, n in H.CLASSES.items())
    classes_hint = "".join(
        f'<p class="class-hint" data-klass="{e(k)}">{e(H.CLASSES[k])} — {e(t)}</p>'
        for k, t in H.CLASS_HINT.items())
    backgrounds = "".join(
        f'<option value="{e(k)}"{" selected" if v.get("background") == k else ""}>{e(n)}</option>'
        for k, n in H.BACKGROUNDS.items())
    abilities = "".join(ability_row(k, s) for k, s in base.items())

    err = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    body = f"""
    <a class="back" href="/heroes">← Мои герои</a>
    <h1>Новый герой</h1>
    <p class="muted lead">Характеристики — из 27 очков, значение от 8 до 15.
    Хиты, класс брони и модификаторы посчитает движок.</p>
    {err}
    <form method="post" action="/hero/new" class="sheet-form">
      <section class="card">
        <h2>Кто вы</h2>
        <div class="fields">
          {field("name", "Имя", value=v.get("name", ""), hint="Как вас зовут за столом.")}
          <label class="field"><span>Вид</span>
            <select name="species">{species}</select></label>
          <label class="field"><span>Класс</span>
            <select name="klass" id="klass">{classes}</select>
            <small id="class-hint"></small>
          </label>
          <label class="field"><span>Предыстория</span>
            <select name="background">{backgrounds}</select></label>
        </div>
        <div class="hidden-hints">{classes_hint}</div>
      </section>

      <section class="card">
        <div class="card-head">
          <h2>Характеристики</h2>
          <p class="points">Осталось очков: <b id="points-left">27</b></p>
        </div>
        <div class="abilities">{abilities}</div>
        <p class="muted small">Стоимость: 8 — 0, 10 — 2, 12 — 4, 13 — 5, 14 — 7, 15 — 9 очков.</p>
      </section>

      <div class="form-actions">
        <button class="btn primary" type="submit">Создать героя</button>
        <a class="btn ghost" href="/heroes">Отмена</a>
      </div>
    </form>

    <script>
    (() => {{
      const COST = {{8:0,9:1,10:2,11:3,12:4,13:5,14:7,15:9}};
      const rows = [...document.querySelectorAll('.ab-row')];
      const left = document.getElementById('points-left');
      const mods = {{json_mods}};
      function total() {{
        return rows.reduce((s, r) => s + COST[+r.querySelector('[data-value]').textContent], 0);
      }}
      function paint() {{
        left.textContent = 27 - total();
        left.classList.toggle('bad', total() > 27);
        rows.forEach(r => {{
          const v = +r.querySelector('[data-value]').textContent;
          r.querySelector('input').value = v;
          r.querySelector('[data-mod]').textContent = (mods[v] >= 0 ? '+' : '') + mods[v];
        }});
        rows.forEach(r => {{
          const v = +r.querySelector('[data-value]').textContent;
          r.querySelector('[data-step="1"]').disabled = v >= 15 || total() + (COST[v+1]-COST[v]) > 27;
          r.querySelector('[data-step="-1"]').disabled = v <= 8;
        }});
      }}
      rows.forEach(r => r.addEventListener('click', ev => {{
        const b = ev.target.closest('[data-step]'); if (!b || b.disabled) return;
        const out = r.querySelector('[data-value]');
        const v = +out.textContent + (+b.dataset.step);
        if (v < 8 || v > 15) return;
        if (total() + (COST[v]-COST[+out.textContent]) > 27) return;
        out.textContent = v; paint();
      }}));
      const hint = document.getElementById('class-hint');
      const sel = document.getElementById('klass');
      function paintHint() {{
        const n = sel.value;
        const src = [...document.querySelectorAll('.class-hint')].find(p => p.dataset.klass === n);
        hint.textContent = src ? src.textContent : '';
      }}
      sel.addEventListener('change', paintHint); paintHint(); paint();
    }})();
    </script>"""
    mods_json = "{" + ",".join(f"{i}:{H.modifier(i)}" for i in range(1, 31)) + "}"
    body = body.replace("{json_mods}", mods_json)
    return layout("Новый герой", body, user=user)


def hero_page(hero_row, abilities: dict, sheet: dict, user=None) -> str:
    s = H.summary(hero_row)
    skills = "".join(
        f"<li><span>{e(H.SKILLS[k][0])}</span>"
        f"<b>{H.skill_modifier(abilities, sheet, k):+d}</b></li>"
        for k in sorted(sheet.get("skills", []), key=lambda x: H.SKILLS[x][0])
    ) or "<li class='muted'>Нет владений навыками</li>"
    saves = "".join(
        f"<li><span>{e(H.ABILITIES[a])}</span><b>{H.modifier(abilities[a]) + sheet.get('proficiency_bonus', 2):+d}</b></li>"
        for a in sheet.get("saves", []) if a in abilities
    )
    abil = "".join(
        f"<div class='ab'><span>{e(H.ABILITIES[k][:3])}</span><b>{v}</b><i>{H.modifier(v):+d}</i></div>"
        for k, v in abilities.items())
    body = f"""
    <a class="back" href="/heroes">← Мои герои</a>
    <div class="page-head">
      <div>
        <h1>{e(s['name'])}</h1>
        <p class="muted">{e(s['species'])} · {e(s['klass'])} · {e(s['background'])} · уровень {s['level']}</p>
      </div>
      <form method="post" action="/campaign/start" class="inline">
        <input type="hidden" name="hero_id" value="{s['id']}">
        <button class="btn primary" type="submit">Играть</button>
      </form>
    </div>
    <div class="grid two">
      <section class="card">
        <h2>Характеристики</h2>
        <div class="abil-row">{abil}</div>
        <dl class="stats wide">
          <div><dt>Хиты</dt><dd>{sheet['hp_max']}</dd></div>
          <div><dt>Класс брони</dt><dd>{sheet['ac']}</dd></div>
          <div><dt>Бонус мастерства</dt><dd>+{sheet['proficiency_bonus']}</dd></div>
          <div><dt>Скорость</dt><dd>{sheet['speed']} фт</dd></div>
          <div><dt>Пассивная внимательность</dt><dd>{sheet['passive_perception']}</dd></div>
        </dl>
      </section>
      <section class="card">
        <h2>Владения</h2>
        <p class="muted small">Навыки</p>
        <ul class="list">{skills}</ul>
        <p class="muted small">Спасброски</p>
        <ul class="list">{saves}</ul>
        <p class="muted small">Снаряжение</p>
        <ul class="list">{"".join(f"<li><span>{e(n)}</span><b>{q}</b></li>" for n, q in H.equipment_list(sheet)) or "<li class='muted'>пусто</li>"}</ul>
      </section>
    </div>"""
    return layout(s["name"], body, user=user)


# ── приключения ──────────────────────────────────────────────────────────────
def campaigns_page(rows: list, user=None) -> str:
    cards = "".join(
        f"""
        <article class="card camp-card">
          <div class="hero-top">
            <h2>{e(r['title'])}</h2>
            <span class="tag">ход {r['turn']}</span>
          </div>
          <p class="muted">{e(r['hero_name'])} · {e(H.CLASSES.get(r['hero_class'], r['hero_class']))}</p>
          <div class="row">
            <a class="btn primary" href="/play/{r['id']}">Продолжить</a>
            <form method="post" action="/campaign/{r['id']}/delete" class="inline"
                  onsubmit="return confirm('Удалить приключение «{e(r['title'])}»? Лента пропадёт.')">
              <button class="btn ghost" type="submit">Удалить</button>
            </form>
          </div>
        </article>""" for r in rows)
    empty = """
      <div class="empty">
        <h2>Приключений нет</h2>
        <p class="muted">Возьмите героя и начните — сцена откроется сразу.</p>
        <a class="btn primary" href="/heroes">К героям</a>
      </div>"""
    body = f"""
    <div class="page-head"><h1>Приключения</h1></div>
    <div class="grid">{cards or empty}</div>"""
    return layout("Приключения", body, user=user)


# ── игровой экран ────────────────────────────────────────────────────────────

def play_page(campaign, messages: list, sheet: dict, abilities: dict | None = None) -> str:
    hp = sheet.get("hp_current", sheet.get("hp_max", 0))
    hp_max = sheet.get("hp_max", 0)
    feed = []
    for m in messages:
        if m["role"] == "master":
            feed.append(f'<div class="msg master"><p class="who">Мастер</p>'
                        f'<div class="prose">{_paras(m["text"])}</div></div>')
        elif m["role"] == "roll":
            raw = m["text"]
            verdict = ""
            for word, cls in (("успех", "ok"), ("провал", "fail")):
                if raw.endswith("— " + word):
                    raw = raw[: -(len(word) + 2)]
                    verdict = f'<span class="verdict {cls}">{word}</span>'
            feed.append(f'<div class="msg roll"><div class="roll-line">{e(raw)}'
                        f'{verdict}</div></div>')
        else:
            feed.append(f'<div class="msg player"><p class="who">{e(campaign["hero_name"])}</p>'
                        f'<div class="prose">{_paras(m["text"])}</div></div>')

    abilities = abilities or {}
    skills = sheet.get("skills") or []
    saves = sheet.get("saves") or []
    gear = H.equipment_list(sheet)
    side = ""
    if abilities:
        abil_html = "".join(
            f'<div class="ab"><span>{e(H.ABBR.get(k, k))}</span>'
            f'<b>{int(abilities.get(k, 10))}</b>'
            f'<i>{H.modifier(int(abilities.get(k, 10))):+d}</i></div>'
            for k in H.ABILITIES)
        skills_html = "".join(
            f"<li><span>{e(H.SKILLS.get(s, (s,))[0])}</span>"
            f"<b>{H.skill_modifier(abilities, sheet, s):+d}</b></li>" for s in skills) \
            or '<li class="muted">нет владений</li>'
        saves_html = "".join(
            f"<li><span>{e(H.SAVE_SHORT.get(s, s.upper()))}</span>"
            f"<b>{H.modifier(int(abilities.get(SAVE_KEY.get(s, s), 10))) + sheet.get('proficiency_bonus', 2):+d}</b></li>"
            for s in saves) or '<li class="muted">нет владений</li>'
        gear_html = "".join(
            f"<li><span>{e(n)}</span><b>{q}</b></li>" for n, q in gear) \
            or '<li class="muted">пусто</li>'
        side = f"""
      <aside class="play-side" aria-label="Лист героя">
        <div class="side-block">
          <p class="side-title">Характеристики</p>
          <div class="abil-row compact">{abil_html}</div>
        </div>
        <dl class="stats">
          <div><dt>Хиты</dt><dd>{hp} / {hp_max}</dd></div>
          <div><dt>Класс брони</dt><dd>{sheet.get("ac", "—")}</dd></div>
          <div><dt>Мастерство</dt><dd>+{sheet.get("proficiency_bonus", 2)}</dd></div>
          <div><dt>Скорость</dt><dd>{sheet.get("speed", "—")} фт</dd></div>
          <div><dt>Внимательность</dt><dd>{sheet.get("passive_perception", "—")}</dd></div>
        </dl>
        <div class="side-block">
          <p class="side-title">Навыки</p>
          <ul class="list tight">{skills_html}</ul>
        </div>
        <div class="side-block">
          <p class="side-title">Спасброски</p>
          <ul class="list tight">{saves_html}</ul>
        </div>
        <div class="side-block">
          <p class="side-title">Снаряжение</p>
          <ul class="list tight gear">{gear_html}</ul>
        </div>
      </aside>"""

    body = f"""
    <div class="play" id="play">
      {side}
      <div class="play-main">
        <header class="play-top">
          <a class="back" href="/campaigns" aria-label="К приключениям">←</a>
          <div class="play-title">
            <b>{e(campaign["title"])}</b>
            <span class="muted">{e(campaign["hero_name"])} · {e(H.CLASSES.get(campaign["hero_class"], campaign["hero_class"]))}</span>
          </div>
          <div class="play-hp" title="Хиты: {hp} из {hp_max}" aria-label="Хиты: {hp} из {hp_max}">
            <span class="hp-val">{hp}</span><span class="hp-sep">/</span><span class="hp-max">{hp_max}</span>
          </div>
        </header>
        <div class="feed" id="feed">{"".join(feed)}</div>
        <form class="composer" method="post" action="/play/{campaign["id"]}/act" id="composer">
          <label class="sr" for="action">Что вы делаете</label>
          <textarea id="action" name="action" rows="1" maxlength="600"
                    placeholder="Что делаете?" autocomplete="off" required></textarea>
          <button class="send" type="submit" aria-label="Отправить">↵</button>
        </form>
      </div>
    </div>
    <script>
    (() => {{
      const ta = document.getElementById('action');
      const feed = document.getElementById('feed');
      feed.scrollTop = feed.scrollHeight;
      ta.focus({{preventScroll: true}});
      ta.addEventListener('input', () => {{
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
      }});
      ta.addEventListener('keydown', e => {{
        if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); ta.form.requestSubmit(); }}
      }});
      document.getElementById('composer').addEventListener('submit', () => {{
        // readonly, а не disabled: выключенное поле не уходит в форму.
        ta.readOnly = true; ta.placeholder = 'Мастер отвечает…';
      }});
    }})();
    </script>"""
    return layout(campaign["title"], body, bare=True)


def _paras(text: str) -> str:
    parts = [p.strip() for p in str(text).split("\n\n") if p.strip()]
    return "".join(f"<p>{e(p)}</p>" for p in parts)


def not_found(what: str = "Страница не найдена", user=None) -> str:
    body = f"""
    <div class="empty">
      <h1>{e(what)}</h1>
      <p class="muted">Возможно, ссылка устарела или запись удалили.</p>
      <a class="btn primary" href="/heroes">К героям</a>
    </div>"""
    return layout(what, body, user=user)
