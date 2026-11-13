# СОСТОЯНИЕ РАБОТЫ — точка продолжения

Обновлено после коммита `f5c569e` (r113a). Ветка `work/nat20-engine`.

## Катч 2026-10-29: движок не падал по 12 классам — врал `.venv`

Симптом: `test_nat20_level_twenty` → 12 failed из 12, ошибка
`'FeatureChoice' object has no attribute 'source'`.

Причина НЕ в коде: `vendor/` был правильный, а `.venv/lib/.../dnd5e_engine/`
держал копию с полусломанной правкой (написанной на несуществующем поле
`FeatureChoice.source`; такого поля в схеме нет и не было).
Тесты идут из site-packages, не из vendor.

**Правило: после правки любого `.py` в `vendor/` — синхронизировать `.venv`.**
Скрипт: `/home/hermes/.hermes/cache/scratch/sync_engine.sh`
(копирует engine + srd-data, чистит `__pycache__`, проверяет импорт).

### Дефект 54 — ЗАКРЫТ (r113a)

Причина была не в «втором стиле», а в том, что каждый пик засчитывался
каждому пулу, который его НАЗЫВАЕТ: Fighter (ур.1) и Champion (ур.7)
называют одни и те же четыре стиля, плюс Human — свой пул origin.

Теперь источник = (документ, уровень), считается отдельно: сначала пики,
которые не называет никто другой, затем общие — самым тесным пулом первыми.
`PATCHES.json` перепинен. `test_nat20_level_twenty`: **14 passed**.

`tests/test_builder_score_bounds.py` — 9 passed (после перепина SHA).

### Открытый вопрос по `place.checks` (требует решения владельца)

Проверок из книги в сценарии нет вообще: `create_experimental_campaign(...)`
получает `checks=None` из `prototype_prepared.launch_prepared`, а
`preparation.launch` (главный путь из `web/preparation.js`) `checks`
не передаёт вовсе.
`CHECKS` в `nat20_content.py` — заглушка: все навыки Сл 15, perception 12.
Проверки в игре — это `_BASE_CHECKS()`, а не материал книги.
Значит «проверка из книги» — не мелкий баг, а незаполненный пайплайн
подготовки приключения.

---

## 1. С чего продолжать (единственный открытый пункт в работе)

**Дефект 54: воин не может взять второй Fighting Style.**

Состояние: **не решён**. Правка откачена, код в исходном виде.

Что известно точно (проверено исполнением):
- Пул класса `fighter`: `['archery','defense','great-weapon-fighting','two-weapon-fighting']`,
  шаг ур.1 `count=1`, остальные шаги `count=0`.
- У подкласса `champion` СВОЙ такой же пул (те же 4 стиля).
- `_check_choice_quotas` (`build_spec.py:804`) получает ВСЕ выборы и прогоняет их
  по каждому источнику; в итоге два законных стиля (по одному из класса и подкласса)
  сверяются с одним пулом и получают отказ.
- Тест: `tests/test_nat20_level_twenty.py::test_every_class_reaches_level_twenty_with_valid_choices[fighter]`
  — падает с `takes at most 1 ... got 2: ['archery','defense']`.

**Две попытки, обе НЕУДАЧНЫ — не повторять:**
1. `+1` к квоте при `replacement=True` → отказ переехал на подкласс `'champion'`
   (13 passed / 1 failed — формулировка ошибки сменилась, причина та же).
2. «Первый пул забирает выборы» (`charged`-множество, выборы списываются по порядку)
   → сломало 12 других тестов (13 failed / 1 passed). Откачено `git checkout`.

**Направление, которое осталось проверить:** считать `taken` не по общему списку
`picks`, а по тому, какие выборы реально относятся к ЭТОМУ источнику
(`choice.source`), либо дать каждому пулу собственный слот и сверять
сумму по пулам, а не по глобальному списку. Перед правкой читать
`_feature_choice_options` (`build_spec.py:795`) и `parse_selected_choices`.

---

## 2. Главный результат сессии

| Этап | Результат |
|---|---|
| Как считали раньше | «регресс» = 19 файлов из **141** |
| Первый полный прогон всех 141 | **149 failed / 799 passed** |
| После изоляции загрузчика корпуса | **13 failed / 935 passed** |

**Причина 135 из 149 провалов (закрыта):** `configure_lib_loader` ставит загрузчик
корпуса на весь процесс. Файл, поставивший свой корпус и не убравший его, ломал
следующий (`unknown background: 'soldier'/'acolyte'/'sage'`). Лечение:
autouse-фикстура в `tests/conftest.py`, возвращающая `BundledAssetLoader()`
после каждого теста. Дефекты не маскируются — утечка проявится при изолированном
запуске.

Полный прогон идёт ~29 минут. Список файлов: `runtime\_files.txt`.

---

## 3. Закрытые дефекты

**Ранние (до этой сессии): 47–50.**
- 47 — каст без цели тратил слот.
- 48 — проверка целостности не знала о коррекциях корпуса, роняла исправный корпус.
- 49 — тесты кодировали неверное правило хода (действие не завершает ход).
- 50 — `test_nat20_playable_rules.py` зависел от чужого проекта (`rules_data`).

**В этой сессии: 51–53.**
- **51 — предметные призывы не исполнялись вообще.** У `ivory-goats` четыре настоящих
  `SummonActivity` (`kind=summon`, профиль с uuid стат-блока), но источник читался
  только из `spell_id`/`feature_id`; для `use_item` источник — `item_id`.
  Исправлено в `_conjuration_carrier` (orchestrator.py:11544) и
  `_conjures_a_combatant` (orchestrator.py:10601) + `'ivory-goats': 'summon'`
  в `CONJURATION_ALLOWLIST` (`activities/conjuration.py:60`).
- **52 — `unknown_spell` уходил игроку по-английски**: коды отказа эмитятся, но его
  не было в `FAILURE_REASONS` (`labels.py`). Добавлен перевод. Попутно: `'json'`
  в тесте — ложное срабатывание на `dump(mode="json")`; регулярка теста теперь
  исключает этот аргумент, а не переводит слово.
- **53** — ожидание `languages == ()` игнорировало `common` от предыстории солдата.

---

## 4. Открытые дефекты (с вердиктами)

1. **Урон гоблина — пробел в ДАННЫХ, не в коде.** У `goblin-warrior` второй урон
   «1d4, если бросок атаки шёл с преимуществом» записан ТОЛЬКО в тексте
   `description`; в `damage.parts` одна часть (1d6). Движок берёт механику из
   `parts`, поэтому наездник не применяется никогда. Закрывать правкой теста
   НЕЛЬЗЯ (§2, §13). Варианты: добавить механическую часть в корпус с объявлением
   коррекции в `CORPUS.json:corrections`, либо объявить ограничение честно.
   Следствие: `test_nat20_api.py::test_native_goblin_hit_uses_projected_bonus_and_damage`.
2. **`Nat20 initial replay diverged`** — `test_nat20_dash.py::test_closed_contract_old_fixture_and_legacy_boundary`
   и `test_nat20_bless.py::test_pending_release_isolated_from_preview7_and_production`.
   Вердикт (проверен): **устаревшая фикстура, не регрессия движка**. В `Combatant`
   добавили 7 полей (`attuned_items`, `chosen_features`, `hit_dice_remaining/total`,
   `ignored_difficult_terrain`, `languages`, `weapon_masteries`) — коммиты
   `1d47d9c`, `37a93dc`, `474dbf8`, `b5e8562`. Плюс `result_digest` для `attack`
   дрейфовал по форме события (`move` совпадает — подтверждение гипотезы).
   Чинить целиком: пересчитать `initial_digest` + все `result_digest` + перезаписать
   `view`/`active_effects`, либо ввести версию схемы боя в `nat20_worker.py:91`.
3. **`_reject_untargeted_cast`** (мой код из r107, `orchestrator.py:14327`) отвергает
   ЛЮБОЙ `cast_spell` с `target_ids` и без `target_id`, хотя `_count_scaled_targets`
   `target_ids` читает. Замерено: `target_ids=['rat-a','rat-b','rat-b']` →
   `target_invalid`, слот не тронут; `target_id='rat-a'` → `SpellCast` + 3
   `DamageApplied`. Мультицелевые касты законны — гейт надо СУЗИТЬ, а не убирать.
   Влияет: `test_nat20_prototype.py::test_diverse_native_build_encounter_action_restart[wizard]`.
4. **`test_nat20_item_rest_recovery.py::test_multiclass_counts_every_class`** —
   устаревшее ожидание: CHA=12, паладин требует STR+CHA≥13 (правило введено
   `aea7d5a`, позже теста). Чинить фикстуру (CHA≥13), не движок.

---

## 5. Требует пользователя / интегратора

- **§14.10** — содержание готово и проверяется, но **принятия интегратора НЕТ**.
  Объявлять принятым нельзя. Перенос делает другой чат.
- **Критик критика** за весь цикл не выдал вердиктов — докладывать честно как недоступного.

---

## 6. Как запускать

```powershell
cd "C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI-Nat20"
$env:SOLO_TEST='1'; $env:PYTHONUTF8='1'
# один файл
.\.venv\Scripts\python.exe -m pytest -q --no-header --tb=line -p no:cacheprovider <файл>
# полный прогон
$all = Get-Content runtime\_files.txt
.\.venv\Scripts\python.exe -m pytest -q --no-header --tb=line -p no:cacheprovider @all
```
Долгие прогоны — только в фоне; результат в `runtime\_full_all*.txt`.

**Дисциплина (нарушение = испорченная история):**
- `orchestrator.py` и `solo/nat20_prototype.py` — CRLF. После правки скриптом
  проверять `(t.match(/\r\n/g)).length === (t.match(/\n/g)).length`.
- После ЛЮБОЙ правки файла движка — перепинить `vendor/nat20/PATCHES.json`
  (`files[].sha256`). Иначе весь публичный путь падает с
  `Content differs from pinned SHA`. Так уже случилось в r111 — заметил субагент.
- Не коммитить: `reports/prototype-3/*/cache/*.sqlite3`,
  `reports/ENGINE_TEST_BASELINE.md`, `reports/PHB2024_ENGINE_BASELINE.md`,
  `_critic_run_mech.txt`, `pytest-of-*/ `, `tmp*/ `, `runtime/`.
