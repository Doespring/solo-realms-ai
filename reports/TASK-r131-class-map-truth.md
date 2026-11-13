# ЗАДАЧА r131 · Карта классов: снять ложный `blocked`, доказав исполнением

Дата: 30.10.2026. Постановщик — прораб (`default`). Исполнитель — `coder`.
Дерево: `/home/hermes/projects/SoloRealmsAI-Nat20`, ветка `work/nat20-engine`.
Стартует ПОСЛЕ r130 (зависимость). Windows-пути игнорировать.

> Один класс работы: **правда о покрытии классов**, а не новые механики.
> Здесь не пишется ни одного нового калькулятора: задача — привести 201 запись
> `rules_data/v2/chapters/phb-03-classes` в соответствие с тем, что движок
> реально делает, и поставить сторож, чтобы карта не начала врать снова.

---

## 0. Что я уже проверил сам (перепроверять не надо)

Прогон `derive_sheet(spec, loader=BundledAssetLoader())` на установленном
движке этого дерева, человеческая предыстория `soldier`, все характеристики 13:

```
barbarian  L1:ok feats=6 hp=13 | L3:ok feats=9 hp=29 | L5:ok feats=11 hp=45
bard       L1:ok feats=5 hp=9  | L3:ok feats=7 hp=21 | L5:ok feats=8  hp=33
cleric     L1:ok feats=5 hp=9  | L3:ok feats=6 hp=21 | L5:ok feats=7  hp=33
druid      L1:ok feats=6 hp=9  | L3:ok feats=8 hp=21 | L5:ok feats=9  hp=33
fighter    L1:ok feats=6 hp=11 | L3:ok feats=8 hp=25 | L5:ok feats=10 hp=39
monk       L1:ok feats=5 hp=9  | L3:ok feats=9 hp=21 | L5:ok feats=12 hp=33
paladin    L1:ok feats=6 hp=11 | L3:ok feats=9 hp=25 | L5:ok feats=11 hp=39
ranger     L1:ok feats=6 hp=11 | L3:ok feats=8 hp=25 | L5:ok feats=9  hp=39
rogue      L1:ok feats=7 hp=9  | L3:ok feats=9 hp=21 | L5:ok feats=11 hp=33
sorcerer   L1:ok feats=5 hp=7  | L3:ok feats=7 hp=17 | L5:ok feats=8  hp=27
warlock    L1:ok feats=5 hp=9  | L3:ok feats=6 hp=21 | L5:ok feats=6  hp=33
wizard     L1:ok feats=6 hp=7  | L3:ok feats=7 hp=17 | L5:ok feats=8  hp=27
```

Ни одного падения: **все 12 классов дают лист и умения уже сейчас**, при том
что в карте все 201 правило помечены `blocked`. Это не «движок не умеет», это
неразобранная опись — документ отстал от кода.

Свежая матрица апстрима (снята 30.10.2026,
https://tapestria.github.io/nat20/capabilities/):

| Механика | Статус |
|---|---|
| Class, subclass, level 1–20, species | ✅ Resolved |
| HP, AC, hit dice, skill/save proficiencies | ✅ Resolved |
| Weapon mastery (2024) | ✅ Resolved |
| Sneak Attack | ✅ Resolved |
| Fighting Style feats | ✅ Resolved |
| Class/species feature activities (Rage, Second Wind, …) | ⚠️ Partial |
| Martial Arts / Monk's Focus | ⚠️ Partial |
| Rage | ⚠️ Partial |
| Bardic Inspiration | ⚠️ Partial |
| Wild Shape | ⚠️ Partial |
| Ability scores, proficiency, expertise | ⚠️ Partial |
| Feats | ⚠️ Partial |

## 1. ЦЕЛЬ

Каждое из 201 правил карты классов получает **доказанный** статус: исполняется
движком — `engine_subset_verified` с хендлером; исполняется частично —
`adjudication_required` с названной в `blockers` дырой; не исполняется —
`blocked` с причиной. Плюс тест-сторож, который падает, если карта обещает
больше, чем исполняется.

## 2. ФАЙЛЫ

| Файл | Что делать |
|---|---|
| `rules_data/v2/chapters/phb-03-classes/*.json` | статусы 201 правила, `implementation`, `handler`, `blockers`, `formal_behavior` |
| `tests/test_phb_classes_map.py` | дополнить сторожем «статус в карте = исполнение» (файл уже есть, 13 passed) |
| `tests/test_class_coverage_proof.py` | **новый файл**: доказательства по 12 классам (см. приёмку) |
| `reports/ENGINE_WORKSPACE_PROGRESS.md`, `STATE.json` | раздел r131 и обновление `proven`/`not_proven` |

## 3. НЕ ТРОГАТЬ

- `vendor/**`, `vendor/nat20/PATCHES.json` — правок движка нет. Если чувствуешь,
  что без правки движка правило не закрыть, — запиши это в отчёт отдельной
  строкой и оставь `blocked`, а не правь вендор.
- Экран, социальные действия, r129/r130.
- **Статус ставится только из закрытых `enum`:** `readiness.status` ∈
  {`adapter_verified`, `kernel_verified`, `engine_subset_verified`,
  `adjudication_required`, `blocked`}; `implementation` ∈
  {`existing_engine_subset`, `kernel_only_unexecuted`, `not_implemented`,
  `kernel_verified`}. Своё слово даёт `JSON Schema rejected instance` без
  имени поля — и полчаса потерь.
- Чужие незакоммиченные файлы. Никаких `git add .`, `git checkout --`,
  `reset --hard`, `git clean`.

## 4. ЧТО ИМЕННО СДЕЛАТЬ

1. **Сначала исполнение, потом статус.** Для каждого правила — короткая
   проверка, чем оно подтверждается: `derive_sheet` (лист/числа), activity
   умения (`use_feature` в живом бою), отдельный механизм (`Sneak Attack`,
   `Weapon mastery`, `Fighting Style`). Статус без такой строки не ставить.
2. **Считать по классам, а не по названиям.** Пройти 12 классов × уровни
   1/3/5/11/20 (умения подкласса — только те ветки, что уже описаны в карте;
   остальные остаются `blocked` с причиной «умения подкласса не расписаны»).
3. **Частичное — называть частичным.** Rage, Bardic Inspiration, Martial Arts,
   Wild Shape, Monk's Focus у апстрима `Partial`: если в карте правило целиком,
   правильный статус — `adjudication_required` с дырой в `blockers`, а не
   зелёный.
4. **Не трогать числа и страницы**: `rule_parameters`, `source.pdf_page`
   сверены в r123, их не переписывать.
5. **Сторож.** Тест обязан ловить расхождение двух источников: для каждого
   правила со статусом «исполняется» — исполнение удаётся; для `blocked` —
   исполнение не заявляется. Проверить сторож **мутацией**: временно подменить
   статус в рабочем JSON → тест краснеет → откатить байт в байт (sha256).
   Сторож без мутационной проверки не считается (см. `scripts/mutation_check.py`).
6. **Ничего не «подгонять под зелёное».** Массовая замена `blocked` на
   зелёный без доказательства — худший исход этой задачи; такой отчёт
   принимается как провал.

## 5. ПРИЁМКА (без вывода команды задача не закрыта)

```
cd /home/hermes/projects/SoloRealmsAI-Nat20
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_class_coverage_proof.py -q --tb=short
SOLO_TEST=1 .venv/bin/python -m pytest tests/test_phb_classes_map.py tests/test_phb_chapter_one.py tests/test_schema_validator.py -q
```

`test_class_coverage_proof.py` обязан содержать:

- по всем 12 классам — `derive_sheet` на уровнях 1/3/5/11/20 без падений
  (с числами: hp, число умений);
- минимум по одному классовому умению на класс, реально применённому в бою
  через `PlayerIntent(intent_type='use_feature', feature_id=…)`
  (Второе дыхание, Ярость, Скрытая атака, Бардовское вдохновение и т.д. —
  что движок действительно принимает; отказ оформляется как честное
  ограничение, а не как зелёный тест);
- сторож соответствия «карта ↔ исполнение» с доказанной мутацией.

Плюс итоговая строка: сколько правил было `blocked` (было 201) и сколько стало
по каждой категории.

## 6. ОТЧЁТ (15 строк max)

```
СДЕЛАНО: <строка>
ТАБЛИЦА СТАТУСОВ: блокировано было 201 → стало: engine_subset_verified=N,
  adjudication_required=N, blocked=N (причина-пример)
ДОКАЗАТЕЛЬСТВА: <файл/тест → числа>
МУТАЦИЯ СТОРОЖА: <что подменил → какой тест упал → откат подтверждён sha256>
ГДЕ НЕ УВЕРЕН: <что прорабу проверить руками>
КОММИТ: <sha>
```

## 7. ТОЧКА ПРОДОЛЖЕНИЯ

Раздел «r131» в `reports/ENGINE_WORKSPACE_PROGRESS.md`; в `STATE.json` из
`not_proven` убрать строку «классовые механики в игре: все 201 правило blocked»
и записать, что теперь доказано, а что осталось (умения подклассов, дыры
Partial). Следующая по плану карточка — «Происхождения (PDF 178-199) и Черты
(200-213)»; она стартует с этого раздела.
