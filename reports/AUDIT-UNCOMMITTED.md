# Аудит незакоммиченных изменений

База сравнения: **HEAD `b881148`** — «Add generic PDF source index and explicit D&D rule coverage registry» (2026-10-16).
Дата аудита: 2026-10-22. Всё ниже проверено командами `git status/diff` и чтением файлов, а не пересказом.

---

## 1. Объём

| Категория | Позиций |
|---|---|
| Изменённые отслеживаемые (M) | 77 |
| Удалённые отслеживаемые (D) | 18 |
| Добавлен и изменён (AM) | 1 |
| Новые неотслеживаемые (??) | 388 |
| **Всего** | **484** |

Неотслеживаемых `.py` — **180**, из них **37 — продуктовый код в `solo/`**.

---

## 2. Главный риск: продуктовый код вне git

**37 файлов `solo/*.py` не отслеживаются git вообще.** Вся сегодняшняя работа — Nat20, импорт листа, прототип, экраны — держится на файлах, которых нет в истории:

`build_identity, cloud_context, command_backend, command_engine, content_verification_cache,
engine_memory, engine_memory_contracts, episode_plan, first_episode, game_commands,
hybrid_rules, hybrid_rules_api, lantern_scene, mechanics_mapping, nat20_api, nat20_bless,
nat20_campaign, nat20_checks, nat20_content, nat20_contracts, nat20_import, nat20_kit,
nat20_levelup, nat20_monsters, nat20_prototype, nat20_screen, nat20_wizard, nat20_worker,
open_settings, play_screen, playable_ui, preview_mock, progression, prototype_gm,
prototype_prepared, prototype_profile, prototype_ui, second_episode`

**Следствие, которое я обязан назвать прямо:** мои правки в `solo/nat20_import.py` (русские названия классов и видов, `RU_NAMES`) и `solo/build_identity.py` **не видны в `git diff`**, потому что сами файлы не отслеживаются. При любой чистке или потере рабочей копии они исчезнут безвозвратно. Это не гипотеза: `git ls-files --error-unmatch solo/nat20_import.py` возвращает «не найдено».

---

## 3. Мои изменения в этом сеансе

**Продуктовый код (отслеживается):**

| Файл | Строк | Что сделано |
|---|---|---|
| `solo/app.py` | +60 / −7 | `asyncio.to_thread(build_identity)` — перестал блокировать цикл событий; плюс чужие правки рядом |
| `web/play.js` | +26 / −7 | ошибка потока теперь показывает причину игроку (`d.message`) вместо молчаливого тоста |
| `solo/cloud.py` | +66 / −21 | содержит и чужие правки — **целиком не откатывать** |

**Не отслеживается (мой дефект видимости):** `solo/nat20_import.py` (`RU_NAMES` + учёт псевдонимов в `_pick`), `solo/build_identity.py` (отвязка stdin).

**Мои новые тесты (7 файлов):** `test_critic_sheet_ru`, `test_nat20_import_russian_sheet`, `test_server_restart_real`, `test_session_continuity_restart`, `test_sse_503_ui`, `test_starting_level_above_one`, `test_wizard_import_route`.

---

## 4. Чужие изменения (сохранять, не откатывать)

Крупнейшие по объёму, отслеживаемые:

| Файл | Строк |
|---|---|
| `solo/prepared_world.py` | +1522 / −57 |
| `solo/game.py` | +297 / −1 |
| `solo/preparation_pipeline.py` | +268 / −20 |
| `solo/desktop_observations.py` | +147 / −2 |
| `solo/combat_service.py` | +75 / −69 |
| `solo/first_playable.py` | +127 / −6 |
| `solo/cloud_cost.py` | +101 / −11 |
| `solo/memory.py` | +89 / −6 |
| `solo/source_extraction.py` | +68 / −5 |

**Важно:** правки в `solo/preparation_pipeline.py` и `solo/prepared_world.py` дают два известных падения `tests/test_unresolved_npc_start.py` («Неизвестная локация NPC»). Это их код, не мой: с откатом моего `solo/app.py` падений становится **три**.

---

## 5. Удаления (18)

- `reports/pdf-pipeline-stage1/` — 7 файлов отчётов (corpus, coverage, validation, логи).
- `solo/character_creation.py`, `character_proposals.py`, `smoke_fixture.py`, `desktop_cloud_smoke.py`, `desktop_creation_smoke.py`.
- `tests/test_character_creation.py`, `test_character_proposals.py`, `test_simple_ux_artistic.py`.
- `web/character-creation.css`, `web/character-creation.js`.

**Проверено на поломки:** ссылок нет ни на `character_proposals`, `smoke_fixture`, `desktop_cloud_smoke`, `desktop_creation_smoke`. Оставшиеся упоминания `character_creation` — строка режима в `solo/ai_roles.py:51`, комментарий в `solo/app.py:394` и список идентичности в `solo/build_identity.py:18`; они неисполняемые.

Из удалённого **моё — только `solo/character_equipment.py`** (7 607 байт, таблица SRD, ноль импортов).

---

## 6. Гигиена репозитория — требует решения

1. **52 временные папки в корне** (`tmp*`, `analyzer-*`) на **19,6 МБ** и **не в `.gitignore`**. Внутри **145 файлов баз** (`*.sqlite3`): 135 в `tmp*`, 10 в `analyzer-*` — данные проб.
2. **`.gitignore` не покрывает** `tmp*/`, `analyzer-*/`, `*.sqlite3`. Единственная правка файла в этой сессии — добавление `.uitmp/`.
3. `adventure.sqlite3` в корне — рабочий файл, не отслеживается.
4. **Секретов не найдено:** совпадения по слову «secret» — это предметы D&D (`magical-secrets.json`, `wand-of-secrets.json`) в `vendor/nat20`.

---

## 7. Риски и что рекомендую

**Высокий.** 37 продуктовых файлов вне git. Один `git clean -fdx` или сбой диска уничтожит работающую игру. Нужен коммит.

**Средний.** `.gitignore` пропускает мусор в корне; коммит «всё подряд» затащит 19,6 МБ баз в историю.

**Низкий.** Мои правки в двух неотслеживаемых файлах визуально неотличимы от «ничего не менялось».

**Рекомендация (нужно ваше слово, сам коммит не делаю):**
1. Добавить в `.gitignore`: `tmp*/`, `analyzer-*/`, `*.sqlite3`.
2. `git add` продуктовые `solo/*.py` (37), мои 7 тестов, изменённые `M`-файлы.
3. Отдельным коммитом — удаления (18).
4. Временные папки не удаляю: там могут быть данные чужих прогонов.
