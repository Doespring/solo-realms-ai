# Проверки из книги: пайплайн `place.checks` (слой `source`)

Статус: **спецификация передачи**, не реализация. Модули `solo/` этой задачей
не менялись. База: `SoloRealmsAI-Nat20`, ветка с HEAD `caacbef` (общий git с
`SoloRealmsAI-app`, отдельный worktree). Все номера строк — по этому дереву на
29.10.2026.

Документ адресован общему потоку (хост) и интегратору Nat20. Правила передачи —
`docs/PARALLEL_WORK_PROTOCOL.md`: контракт описывается здесь, хост сам правит
`preparation.py`, `prototype_prepared.py`, `nat20_api.py` и адаптеры.

---

## 0. Что проверено по коду (факты, на которых стоит вся спецификация)

| # | Факт | Где |
|---|---|---|
| 1 | `launch()` главного пути не принимает и не передаёт проверки: сигнатура `launch(self,id,confirm)` | `solo/preparation.py:203` |
| 2 | Мир собирается в `ScenarioPackage` → `import_package(state,package)` | `solo/preparation.py:246-268` |
| 3 | `WorldLocation` **не имеет поля `checks`** и строг (`Strict`): лишний ключ — `ValidationError` | `solo/world.py:62-82` |
| 4 | `import_package` пишет `value=place.model_dump()` — всё, чего нет в модели, отбрасывается молча | `solo/world.py:281-285` |
| 5 | Nat20-ветка уже пробрасывает `checks=checks`, `checks=None` по умолчанию | `solo/prototype_prepared.py:179-180`, `:381-382` |
| 6 | Единственный вызов, который **передаёт** проверки — `prototype_ui.py:430` `checks=body.typed_checks()`, но `prepared_checks` инициализируется пустым `default_factory=dict` (`prototype_ui.py:39`), и **ни один клиент его не отправляет** (grep по `web/*.js` — 0 совпадений). Канал мёртв. | `solo/prototype_ui.py:36-57`, `:427-431` |
| 7 | Входящая форма у `PrototypeCheck`: `kind=skill|ability|saving_throw`, `skill`, `ability` (**строчные** `str|dex|...`), `dc` 1..40, `source: CampaignSource` — обязательный | `solo/nat20_prototype.py:65-80`, `solo/nat20_campaign.py:27-38` |
| 8 | Реальная сверка идёт из `definition.checks`, **а не** из `state['locations'][i]['checks']`: `checks = definition.checks if definition else CHECKS` | `solo/nat20_worker.py:283-291` |
| 9 | `state['locations'][i]['checks']` читает только legacy-ветка | `solo/command_engine.py:79-80`, `:180-183` |
| 10 | `CHECKS` — заглушка: все навыки Сл 15, `perception` 12, способности/спасброски `15` | `solo/nat20_content.py:25-35` |
| 11 | `make_definition` при пустых `checks` сам заполняет их **всеми** навыками/способностями с `dc=None`, `source=CampaignSource(id='experimental-author')` | `solo/nat20_prototype.py:265-269` |
| 12 | В `WorldNPC` нет ни проверок, ни цитат | `solo/world.py:48-61` |
| 13 | `MechanicCandidate` (навык→характеристика, Сл, цитата, trigger) и `validate_evidence` с проверкой «Сл есть в цитате» **уже существуют** | `solo/adventure_analysis.py:55-64`, `:117-120` |
| 14 | `adventure_analysis` вызывается только вручную из API-ревью (`analysis`-роут), пайплайн подготовки его не использует | `solo/adventure_api.py:152-156` |
| 15 | `annotations` / `provenance` из `compile()` живут в `creation_runs.result` и в кампанию не переносятся | `solo/prepared_world.py:1717`, `solo/preparation_pipeline.py:205-217` |

**Дефект, найденный при проверке (важнее самой передачи):** сверка
`kind='ability'` в Nat20 падает на рантайме —

```
solo/nat20_checks.py:46 → CheckSpec(kind=kind, skill=skill, ability=ability, ...)
ability=None → ValueError: CheckSpec.kind='ability' requires `ability`
```

Проверено запуском: `resolve_state_check(kind='ability', ability=None)` →
`ValueError`; `kind='skill'` и `kind='saving_throw'` работают. Причина —
`nat20_worker.py:288` берёт `check.ability` как есть, а у «чистой» проверки
характеристики поля `skill` нет и `ability` может быть `None`. Значит **любая
проверка характеристики из книги уронит ход с ошибкой движка, пока это не
исправлено**. Это блокирующее условие приёмки (см. §7).

---

## 1. Схема данных: проверка из материала

### 1.1. Форма, которую уже ждёт игра

`command_engine.py:180` берёт `place['checks'][check_id]` и передаёт её в
`Step(kind='check', text=check_id, **value)` (`:183`). `Step` (`solo/contracts.py:9-25`)
принимает `ability`, `dc`, `proficient`, `skill`, `advantage`, `disadvantage` —
лишние ключи ему не нужны, но `Strict` **не позволит** положить в `Step`
посторонние поля. Поэтому провенанс обязан жить рядом, а не внутри.

Итог: в `state['locations'][i]['checks'][check_id]` кладётся **ровно форма
`Step`-совместимая + провенанс в отдельном ключе**:

```json
{
  "ability": "wis",
  "dc": 14,
  "skill": "perception",
  "advantage": false,
  "disadvantage": false,
  "origin": "source",
  "source": {
    "document": "<sha256 файла источника, 64 hex>",
    "page": 11,
    "quote": "Внимательность (Сл 14) замечает кобольда за бочками."
  },
  "trigger": "hero searches the crates behind the counter",
  "anchor": {"kind": "location", "id": "taproom"}
}
```

Правила полей:

- `ability` — **строчный** код (`str|dex|con|int|wis|cha`). Это то, что реально
  уходит в `CheckSpec`.
- `dc` — целое 1..40, **обязательное** для `origin='source'`.
- `skill` — `""`, если проверка «просто характеристика». `Step.skill` по
  умолчанию `''`, поэтому `""` безопасно.
- `origin` — `'source'` | `'master'` (см. §4). Третьего значения нет.
- `source` — только при `origin='source'`, ровно `SourceQuote`-форма
  (`solo/preparation_contracts.py:6-9`: `document`, `page`, `quote`).
- `trigger` — **условие срабатывания** словами книги. Без него надо знать, какой
  id выбирать; с ним Мастер и `available_checks` объяснимы. Ограничение 240
  символов — как у `MechanicCandidate.trigger`.
- `anchor` — `{"kind":"location","id":<place name>}` либо
  `{"kind":"npc","id":<npc id>}` либо `{"kind":"object","id":<SceneObject.id>}`.
  Для места `anchor` избыточен (место и так содержит проверку), но нужен при
  переносе проверки в `definition.checks`, где место знания теряется.

### 1.2. Что нужно в `WorldLocation` (аддитивно, по образцу `interactions`)

`WorldLocation.interactions` (`solo/world.py:68`) — готовый прецедент: строгая
модель + `max_length` + кастомный `import_package`. Добавляется одно поле:

```python
# solo/world.py, после interactions
Checks = dict[str, SceneCheckFromSource] = Field(default_factory=dict, max_length=100)
```

`SceneCheckFromSource(Strict)` повторяет блок 1.1. Валидатор места
(`solo/world.py:77-82`) дополняется: `len(Checks) <= 100` и ключи `id` совпадают
с регуляркой `^[a-z][a-z0-9_-]{0,59}$` (та же, что у `EncounterActor`,
`nat20_campaign.py:42`).

**Почему поле обязано быть в модели, а не «просто ключом в dict»:** `import_package`
делает `place.model_dump()` (`solo/world.py:282`). Проверки, положенные в
словарь в обход модели, будут молча выброшены — это ровно то, что сейчас
происходит с любым полем, которого нет в `WorldLocation`.

### 1.3. Дублирование в `definition.checks`

Факт №8: сверка идёт из `definition.checks`, слой `state['locations']` для него
невидим. Поэтому проверки нужно **продублировать** в оба места:

- `state['locations'][i]['checks']` — для legacy-ветки (`command_engine.py:180`),
  для UI («здесь можно Проверку») и для `play_screen.py:105-144`;
- `definition.checks` — для реальной сверки Nat20 (`nat20_worker.py:283`).

Ключ в `definition.checks` должен быть глобально уникальным (это
`dict[str, PrototypeCheck]`, а не «на место»), поэтому id строится детерминированно:

```python
check_id = f"{anchor_kind}:{anchor_id}:{slug(kind)}:{slug(skill or ability)}:{n}"
```

Пример: `loc:taproom:perception:1`, `npc:gundren:insight:1`,
`loc:cave-mouth:ability:dex:1`. `n` — порядковый номер одноимённой проверки на
этом якоре, начиная с 1. Одинаковый материал всегда даёт одинаковые id —
это нужно для `PrototypeDefinition.definition_sha256` (`nat20_prototype.py:103-109`)
и для сверки «подтверждённая структура мира не изменилась»
(`prototype_prepared.py:214-215`).

---

## 2. Где извлекать

### 2.1. Точка вставки (главная)

`PreparedWorldValidator.compile()` — `solo/prepared_world.py:1075`, разбор
сущностей в `:1139-1170`, сборка мест в `:1248-1302` (`locations[place.name]=place`
на `:1302`), NPC в `:1303-1311`, объекты `:1430-1442`.

Извлечение проверок встаёт **между** разбором сущностей и `import_package`, ровно
по образцу объектов:

```python
# после цикла объектов, prepared_world.py ~:1442 (перед nodes=... на :1443)
from .checks_from_source import extract_place_checks
place_checks, check_notes = extract_place_checks(accepted, provenance, self._page_cache, locations)
for place, checks in place_checks.items():
    locations[place].checks = {**locations[place].checks, **checks}
annotations.extend(check_notes)
```

`extract_place_checks` — новый модуль (его пишет движок, не хост), использует уже
готовые инструменты дерева:

- `MechanicCandidate` + `validate_evidence` (`adventure_analysis.py:55-64`,
  `:117-120`) — берёт `ability`, `dc`, `quote`, `trigger` и сам доказывает, что
  «Сл N» реально напечатано в цитате;
- `names_place` / `place_by_kind` / `normalized` (`prepared_world.py:584-590`,
  `:286-300`, `:58`) — привязка проверки к месту;
- `_matched_sentence` (`:714`) — отсев проверок, чьё условие принадлежит другому
  предложению той же страницы (иначе «Сл 14» и «за бочками» склеятся из разных
  абзацев).

**Откуда берутся кандидаты.** Здесь и главный разрыв: пайплайн подготовки
(`preparation_pipeline.py:218-291`) `AdventureAnalysis` **не вызывает** — он
работает только из ручного роута (`adventure_api.py:152-156`). Два честных пути:

- **П1 (дешёвый, локальный, без ИИ).** Регулярный проход по `_page_cache` того же
  места: `Сл\s*(\d{1,2})` / `DC\s*(\d{1,2})` в пределах предложения, навык —
  по таблице синонимов русского листа (`solo/labels.py` уже содержит названия
  навыков), характеристика — через `_SKILLS` (`nat20_content.py:25-29`).
  Даёт только «напечатанные» Сл и не умеет в `trigger` без глагола. Побочных
  вызовов нет.
- **П2 (полный, платный).** Прокинуть `mechanic_candidates` из последнего
  `pdf_analyst`-прогона по документам места в `compile()`. Уже оплаченные
  результаты лежат в `analysis_cache` (`adventure_analysis.py:174-176`,
  `:163-168`) — их можно читать бесплатно, не запуская модель заново.

Рекомендация: **П2 как источник + П1 как сверка**. Кандидат без подтверждённой
цитаты (`validate_evidence`) отбрасывается в `annotations`, а не в state.

### 2.2. Ветка Nat20

`solo/prototype_prepared.py:307-366` — тот же `import_package(world,package)`.
Если поле в `WorldLocation` появилось (§1.2), проверки доезжают **сами**,
никакого второго прохода не нужно. Дальше:

- мир пришёл из `packages` → `state['locations'][i]['checks']` заполнены;
- `definition.checks` **не заполнены** — их собирает `launch_prepared` из того же
  мира (§3.2), а не из `body.typed_checks()`.

### 2.3. Ручной путь `preparation.text_structure`

`solo/preparation.py:11-40` — разметка `## Локация:` / `## NPC: Имя @ Локация`.
Проверки добавляются новой директивой того же парсера:

```
## Проверка: Внимательность @ Таверна | Сл 14 | заметить кобольда за бочками
```

Цитата здесь — сама строка файла (документ = sha256 загруженного источника),
поэтому `origin='source'` остаётся честным: это то, что автор ввёл как материал.
Без директивы проверок не будет — и это видно в `available_checks`, а не
подменяется Сл 15.

---

## 3. Как передать в кампанию

### 3.1. Минимальный дифф: `preparation.launch` (главный путь)

Ничего не передаётся аргументом — проверки уже внутри `package`, если поле
есть в модели. Требуется только **сохранить и протащить**:

```diff
--- a/solo/world.py
@@ class WorldLocation(Strict):
     interactions: list['SceneObject']=Field(default_factory=list,max_length=80)
+    # Additive, like interactions: a place may name its own checks with their source.
+    checks: dict[str,SourceCheck]=Field(default_factory=dict,max_length=100)
```

```diff
--- a/solo/preparation.py  (launch, цикл пакетов :254-268)
             if any(p.name==body.start_name for p in package.locations):
                 replacement=next(p for p in package.locations if p.name==body.start_name)
+                # The opening replacement must not drop the place's own checks.
+                replacement=replacement.model_copy(update={'checks':{
+                    **replacement.checks,**(next((p.checks for p in package.locations
+                        if p.name==body.start_name),{}))}})
                 replacement=WorldLocation.model_validate({**replacement.model_dump(),'description':body.opening,'known_to_hero':True})
```

Этого достаточно для legacy-ветки (`command_engine.py:180` начнёт находить
проверки) и для UI. Для Nat20 нужен второй кусок — §3.2.

### 3.2. Минимальный дифф: `launch_prepared` → `create_experimental_campaign`

```diff
--- a/solo/prototype_prepared.py  (перед вызовом create_experimental_campaign, :381)
+    # The world carries the checks it printed; the client must not be the
+    # authority on difficulty. composed_checks() also keeps the book's own
+    # checks apart from the generic registry.
+    from .checks_from_source import composed_checks
+    world_checks, check_limitations = composed_checks(world, draft_id, prepared_result)
+    limitations += check_limitations
     current = api.create_experimental_campaign(authored_hero.get('name') or body.name,build=selected,
-        encounters=encounters or {},checks=checks,prepared_spells=prepared_spells,seed=seed,
+        encounters=encounters or {},checks=world_checks or checks,prepared_spells=prepared_spells,seed=seed,
```

`composed_checks(world, draft_id, prepared_result)` (новый модуль) делает три вещи:

1. собирает `PrototypeCheck` из `world['locations'][*]['checks']`, конвертируя
   провенанс в `CampaignSource(id='source:'+document[:12], version='page-'+str(page), note=quote[:800])`;
2. **не** добавляет «универсальный» набор: набор `dc=None` на все навыки уже
   создаётся движком (`nat20_prototype.py:265-269`) — значит пустые проверки не
   ломают существующее поведение, но и не выдают себя за книжные;
3. возвращает человекочитаемые ограничения для `limitations`:
   `'Проверок из книги найдено: N; без Сл (умолчание) не подставлялось.'`

Важно: **`checks` из HTTP-тела не должен перетирать книжные.** Приоритет:
`world_checks` (книга) → `checks` (явный ввод мастера) → пусто (движок
подставит `dc=None`). Порядок в коде выше это и выражает.

### 3.3. Что правит хост, что движок

| Файл | Кто | Что |
|---|---|---|
| `solo/world.py` (`WorldLocation.checks`) | **хост** | аддитивное поле + валидатор |
| `solo/preparation.py:203` | **хост** | сохранить `checks` в стартовом месте (§3.1) |
| `solo/prototype_prepared.py:381` | **хост** | `composed_checks` вместо `checks` |
| `solo/prototype_ui.py:36-57` | **хост** | `typed_checks()` больше не единственный источник; поле `prepared_checks` оставить для песочницы, но помечать `origin='master'` |
| `solo/checks_from_source.py` (новый) | **движок** | `extract_place_checks`, `composed_checks`, `SourceCheck` |
| `solo/prepared_world.py:~1442` | **движок** | вызов извлечения в `compile()` |
| `solo/nat20_checks.py` / `nat20_worker.py:283-291` | **движок** | фикс `kind='ability'` (см. §7) |

---

## 4. «По книге» против «по умолчанию»

Сегодня Сл 15 неотличима от книжной: `CHECKS` (`nat20_content.py:31-35`) и
подстановка `make_definition` (`nat20_prototype.py:265-269`) обе дают цифру без
происхождения. Нужен явный маркер на трёх уровнях:

1. **Данные.** `origin: 'source' | 'master'` + `source{document,page,quote}` (§1.1).
   Проверка без `source` не может иметь `origin='source'` — валидатор это
   проверяет.
2. **Сверка.** В `nat20_worker.py:283` проверка отдаётся с `CampaignSource`,
   уже записанным в `PrototypeCheck.source`. Для книжной — `id` начинается с
   `source:`, для мастерской — `master:`, для реестра — `experimental-author`.
   Это единственное место, которое видит и книжную, и подставленную проверку.
3. **Экран.** `available_checks` сейчас — список id (`command_engine.py:79-80`,
   `nat20_api.py:279`). Добавить параллельный `check_sources: dict[check_id, {origin, page, quote}]`
   в `GameSnapshot` (`engine_memory_contracts.py:73`, поле `available_checks`
   рядом, максимум 100). `nat20_screen.py:65` тогда печатает
   `'Проверить: Внимательность (Сл 14, книга стр. 11)'`, а не просто id.

**Что видит игрок после броска.** `play_screen.py:79-91` собирает `last_roll`,
`web/game-screen.js:39` печатает `label: formula → total · успех/неудача`.
`formula` для legacy-ветки уже содержит «против Сл N» (`rulesets.py:71`), а для
Nat20 `formula` пуст (`game-screen.js:39` → `roll.dice.join`). Требование:
для проверки с `origin='source'` отдавать в `roll` дополнительно
`source_ref='книга, стр. N'`, иначе игрок видит «Проверка: 12 → успех» без Сл —
ровно та слепота, из-за которой задача и появилась.

---

## 5. Что делать с заглушкой `CHECKS`

Предложение: **оставить как аварийный резерв, но переименовать смысл и
пометить.**

1. `nat20_content.py:31-35` — оставить, имя не менять (`CHECKS` импортируется
   в `command_engine.py:13`, `nat20_worker.py:283`, `nat20_api.py:279`,
   `tests/test_check_registry_width.py`). Добавить рядом
   `CHECKS_ORIGIN = 'registry-default'` и комментарий, что Сл 15 — **не**
   правило книги, а «стандартная попытка», и что в кампании это значение
   должно проявляться только как `origin='master'` с явной пометкой.
2. `make_definition` (`nat20_prototype.py:265-269`) — не убирать: на этих
   `dc=None` держится возможность бросать проверку в песочнице без сценария.
   Но при `checks` из книги этот блок **не выполняется** (там `if not checks`),
   значит книжные проверки его автоматически вытесняют — это уже корректно.
3. **Убрать нельзя** именно из-за песочницы и `test_check_registry_width.py`:
   удаление реестра закроет 18 навыков там, где книга ничего не печатает.
4. Единственное, что стоит удалить, — `_BASE_CHECKS()` как **основной** путь
   выдачи проверок в UI (`command_engine.py:79-80`). Предложение: если у места
   нет своих проверок, отдавать **пустой** список и явное поле
   `checks_origin='none'`, а не тихо подставлять Сл 15. Игрок увидит «в этой
   сцене проверок по книге нет», а не выдуманную сложность.

---

## 6. Полный пример сквозного прохода

**Материал (книга, стр. 11, одна строка из read-aloud/текста места «Таверна»):**

> «Внимательность (Сл 14) замечает за бочками кобольда, который ждёт приказа.»

**1. Анализ (П2, уже оплаченный кандидат в `analysis_cache`):**

```json
{"page": 11, "operation": "ability_check", "ability": "wisdom", "dc": 14,
 "quote": "Внимательность (Сл 14) замечает за бочками кобольда, который ждёт приказа.",
 "trigger": "Внимательность", "status": "requires_review", "executable": false}
```

`validate_evidence` (`adventure_analysis.py:117-120`) подтверждает: `Сл 14` есть
в цитате, «мудрост»/`wisdom` и trigger подтверждены.

**2. Извлечение (`extract_place_checks`):**

```json
{
  "loc:taproom:perception:1": {
    "ability": "wis", "skill": "perception", "dc": 14,
    "advantage": false, "disadvantage": false,
    "origin": "source",
    "source": {"document": "<sha256 PDF>", "page": 11,
               "quote": "Внимательность (Сл 14) замечает за бочками кобольда, который ждёт приказа."},
    "trigger": "Внимательность у бочек в таверне",
    "anchor": {"kind": "location", "id": "taproom"}
  }
}
```

**3. Что ложится в state (после `import_package`):**

```json
"locations": [{
  "name": "taproom",
  "description": "…",
  "npc": [], "objects": [], "interactions": [],
  "facts": [], "secrets": [], "known_to_hero": true,
  "checks": {
    "loc:taproom:perception:1": {
      "ability": "wis", "skill": "perception", "dc": 14,
      "origin": "source",
      "source": {"document": "<sha256>", "page": 11, "quote": "Внимательность (Сл 14) …"},
      "trigger": "Внимательность у бочек в таверне",
      "anchor": {"kind": "location", "id": "taproom"}
    }
  }
}]
```

**4. Что попадает в `definition.checks` (Nat20-ветка):**

```json
{"loc:taproom:perception:1": {
  "kind": "skill", "skill": "perception", "ability": null, "dc": 14,
  "source": {"kind": "authored_scenario", "id": "source:<sha256[:12]>",
             "version": "page-11",
             "note": "Внимательность (Сл 14) замечает за бочками кобольда, который ждёт приказа."}
}}
```

**5. Что видит игрок:**

- в списке действий: `Проверить: Внимательность (Сл 14, книга стр. 11)` —
  вместо сегодняшнего `Проверить: perception`;
- после броска (`game-screen.js:39`): `Проверка: d20 +2 = 15 → успех · по книге, стр. 11`;
- если в сцене нет книжной проверки: `Проверок по книге в этой сцене нет`;
- если Мастер добавил свою: `Проверить: Атлетика (Сл 15, мастер)` — Сл 15
  теперь **подписана**, а не выдаёт себя за книжную.

---

## 7. Дыры, которые спецификация обязана закрыть

### 7.1. Проверка относится к NPC, объекту или дороге

У `WorldNPC` нет ни `checks`, ни `source` (`solo/world.py:48-61`), у
`SceneObject` тоже (`solo/interactions.py:10-30`). Три честных случая:

- **Проверка «против» NPC** («Убеждение (Сл 15), чтобы Гундрен рассказал о
  карте») — это проверка **героя**, привязанная к NPC. Хранится в
  `anchor={"kind":"npc","id":"<npc id>"}`, а в state — в месте, где NPC стоит,
  потому что `command_engine.py:180` ищет проверки **только по месту**.
  Ключ id при этом остаётся `npc:gundren:persuasion:1` — так видно, к кому
  относится.
- **Проверка, зависящая от NPC-характеристики** («сравните свою Скрытность с
  Внимательностью стража») — **не поддерживается**: движок не умеет
  противопоставленных проверок (`resolve_state_check` берёт только лист
  героя, `nat20_checks.py:9-15`). Такая запись уходит в `annotations` как
  `'противопоставленная проверка не поддержана'` и в `limitations`, а не
  превращается в обычную Сл-проверку — иначе победит ложь.
- **Проверка на объект** («Сила (Сл 20), чтобы сорвать дверь») — `anchor` на
  `SceneObject.id`, состояние в месте. Дверь имеет `lock/opened`, но проверка
  их не меняет напрямую: успех — это `Step`-результат, а изменение двери —
  отдельная операция `use`. Спецификация фиксирует это как ограничение:
  **проверка не открывает дверь автоматически.**

### 7.2. Проверка на дороге / переходе

`WorldRoute` (`world.py`) не имеет места-владельца, кроме `from_`. Предложение:
проверка перехода хранится в месте `from_` с `anchor={"kind":"route","id":"<from_->to_>"}`.
Иначе проверка «перебраться через реку» потеряет привязку: у маршрута нет
контейнера для проверок, а добавлять его в `WorldRoute` дороже, чем хранить по
началу пути.

### 7.3. Обнаруженный блокер: проверка характеристики падает

См. §0 (последний абзац). Требование приёмки: до включения книжных проверок
характеристик (а `MechanicCandidate` умеет **только** `ability_check`,
`adventure_analysis.py:59`) движок обязан либо

- (а) в `nat20_worker.py:288` подставлять `ability` из `skill` через
  `SKILL_ABILITIES`, а для «чистой» характеристики требовать явный `ability`;
- (б) запретить `kind='ability'` с `ability=None` на уровне `PrototypeCheck`
  и в `composed_checks` конвертировать такие проверки в `kind='skill'`, если
  навык известен.

Рекомендуется (б): валидировать на входе дешевле, чем ловить `ValueError`
посреди хода. Без этого фикса книжная проверка Силы/Ловкости роняет ход.

### 7.4. Проверки из книги не должны терять книгу при повторном запуске

`prototype_prepared.py:188-195` при повторном запуске возвращает кампанию как
есть (replayed). Значит проверки фиксируются один раз при первом запуске —
и это правильно: материал не должен меняться под начатой кампанией. Нужен
тест: после `launch` изменение источника → повторный `launch` → Сл в state
не меняется.

### 7.5. Что ещё не хватает в данных на этом сервере

- `rules_data` и оригиналы книг на VPS нет — реальные цитаты и номера страниц
  приведены как формат, а не как выдержки из конкретного PDF. Сквозной тест
  на живом материале здесь невозможен, его делает хост на Windows.
- `analysis_cache` на этом сервере пуст (базы нет) — путь П2 проверяем только
  по коду, не исполнением. Это названо явно, а не выдано за проверку.

---

## 8. Тесты, которые спецификация обязывает добавить

1. `test_world_location_keeps_checks` — `WorldLocation(checks={...})` валиден,
   а `import_package` переживает `model_dump` без потерь.
2. `test_source_check_requires_quote` — `origin='source'` без `source.quote` →
   `ValidationError`.
3. `test_source_dc_in_quote` — Сл 14 при цитате без «Сл 14» → отказ (переиспользует
   проверку из `adventure_analysis.py:119`).
4. `test_launch_keeps_book_dc` — сквозной: материал с «Сл 14» → `launch` →
   `state['locations'][0]['checks'][id]['dc']==14` и `origin=='source'`.
5. `test_no_silent_default_dc` — материал без проверок → в state нет проверок,
   а `available_checks` не содержит Сл 15 под видом книжной.
6. `test_ability_check_without_ability_refused` — `kind='ability'`, `ability=None`
   → отказ на валидации, а не `ValueError` посреди хода (после фикса §7.3).
7. `test_npc_check_lives_in_its_place` — проверка с `anchor.kind='npc'` находится
   в `state['locations']` того места, где стоит NPC.

---

## 9. Открытые вопросы для владельца (ответить до реализации)

1. **Противостоящие проверки** («Скрытность против Внимательности стража») —
   делаем отдельным слоем (`kind='contest'`) или откладываем? Сейчас они честно
   уходят в ограничения.
2. **Проверка-триггер**: должна ли проверка предлагаться всегда или только при
   выполнении `trigger`? Без второго `available_checks` в людной сцене вырастет
   до десятков кнопок, и выбор станет угадыванием.
3. **Стоимость П2**: чтение `analysis_cache` бесплатно, но если кеша нет —
   разбор книги платный. Запускать ли локальный проход П1 всегда, даже при
   наличии кеша (дешевле и покрывает «Сл N» в прозе)?
