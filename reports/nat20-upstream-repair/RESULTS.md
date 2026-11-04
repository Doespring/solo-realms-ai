# Изолированный эксперимент Nat20: три upstream-дефекта

18.10.2026. **Эксперимент успешен в указанном узком наборе тестов. Bless остаётся
pending. Активация, замена pinned engine и миграция replay НЕ разрешены.**

Все созданные файлы находятся в `reports/nat20-upstream-repair/`. Рабочие vendor,
установленные `dnd5e_engine`/`dnd5e_srd_data`, pins, Solo adapters/DTO, stand,
registry и данные проверены до/после: изменений нет. Python-пакеты не
устанавливались, публичные commit/PR не создавались, платных вызовов и сборок нет.
Полный upstream/Solo suite не запускался. Копии и промежуточные результаты
сохранены; разрушительной очистки не было.

## Патч

Файл: `nat20-three-defects.patch` (рядом файл `.sha256`).

SHA-256:

```text
122c7d88e4c35fd98ac6fa74d57a975d8c47828b9ee95c38055dd9e0a6374ff7
```

Основа — неизменённый Nat20 0.7.0,
commit `e187c813bb4e43c4e4e62942ecd83831f1cdbcaa`.
Патч применён через `git apply --check` и `git apply` **только** к отдельной
копии `work/trial-02/fixed/`. Контрольные суммы остальных файлов копии совпадают
с pinned lock; в `work/trial-02/pinned/` изменений нет.
Сам UPSTREAM.json в экспериментальных копиях не переписывался: исправленная
копия не притворяется официальным или production-pinned выпуском.

Изменены два upstream-файла относительно корня копии:

| Файл | Изменение и переиспользование |
| --- | --- |
| `packages/dnd5e-engine/src/dnd5e_engine/rules/effects.py` | Один общий alias `system.bonuses.abilities.save` → `save.bonus`; прежний `filter_changes_by_bucket` выбирает оригинальные changes по canonical key. Расчёт, modes, приоритеты и кости остаются у `apply_changes_to_check`. |
| `packages/dnd5e-engine/src/dnd5e_engine/orchestrator.py` | Боевая проекция использует тот же alias. Damage→concentration строит существующий context через `_build_hydration_payload`/`build_activity_context`, вызывает прежний effect-aware `roll_save`, сохраняет один `ConcentrationCheck`. LR согласуется прежним идемпотентным `_sync_legendary_resistance`, без повторного списания. |
| Тот же `orchestrator.py` | EffectApplied делает upsert через уже существующий `dedupe_by_identity`: новая точная `(target_id,id,origin)` выигрывает, позиция прочих эффектов сохраняется. Прежние expiry/concentration folds затем снимают единственный экземпляр. |

Нет новой формулы DC, второго d20/save/damage resolver, ручного d4 в Solo,
ручной очистки канона, monkeypatch рабочего engine или нового игрового журнала.
Существующая fallback-ветка отсутствующего combatant оставлена на прежнем
`roll_d20_test`; этот редкий некорректный вход отдельно не сертифицировался.

## Результаты: characterization не равна correctness

Принятый прогон: `work/trial-02/run-02/summary.json`; краткая ссылка на него и
результаты — `latest.json`.

| Копия / набор | Результат | Значение |
| --- | --- | --- |
| Pinned / `test_characterization.py` | 3/3 passed | Подтверждены три прежних дефекта. Это не успешное исполнение правил. |
| Pinned / `test_correctness.py` | 6/16 passed, **10 ожидаемых failures**, 0 errors | Позитивные ожидания действительно обнаруживают старые дефекты, а не просто зеркалят реализацию. |
| Fixed / тот же `test_correctness.py` | **16/16 passed**, 0 failures/errors | Тот же набор ожиданий проходит на исправленной копии. |
| Pinned ↔ Fixed / no-effect traces | **4/4 полностью совпали** | Все выданные события, HP/chain/turn/round, LR-ресурсы, полный hash состояния RNG и следующие четыре draw. |

Не суммировать повторные запуски как дополнительные уникальные проверки.
Артефакты старой попытки `trial-01` и первого прогона `trial-02/run-01`
сохранены. В первой попытке один тест ошибочно сравнивал пустой tuple API с `[]`;
ожидание исправлено на проверку отсутствия эффектов. Финальный прогон использует
16 correctness cases, включая два добавленных пороговых случая LR.

Позитивные проверки покрывают:

- Bless/Bane на концентрационном save: d20 → ровно один signed d4, правильные
  total/success, drop при провале и ожидаемое состояние RNG.
- Один `ConcentrationCheck`, отсутствие второго `SaveRolled`; успех без LR,
  конверсия провала с одним LR и отсутствие повторного списания при двух
  последующих overlapping `_sync_legendary_resistance`.
- Bless переводит исходный провал в успех и **сохраняет** LR; Bane переводит
  исходный успех в провал и **требует** ровно один LR. Итоговое save-событие
  отражает конверсию, событие расхода следует после него.
- Standalone saving throw: реальный Foundry key даёт тот же результат,
  effect breakdown и RNG state, что уже поддержанный `save.bonus`; исходные
  данные эффекта не меняются. Save alias не влияет на skill/ability checks.
- Два настоящих публичных cast Bless: две ячейки потрачены, effect/chain
  остаются в одном экземпляре, следующая атака получает один d4, drop полностью
  снимает effect и chain.
- Разные target/origin не сливаются. Refresh заменяет значение старой точной
  identity, не меняя порядок остальных эффектов.
- No-effect traces: два seed с обычной концентрацией без bonus changes и те же
  два seed с LR — сравнение старой и новой копий без нормализации событий.

Bless загружается из сохранённого ранее typed Foundry 2024 fixture. Для Bane
используется явно обозначенный стендовый signed save-bonus fixture; это не
импорт и не сертификация полного заклинания Bane. Классовый fixture имеет обычные
HP/характеристики, но не выдаётся за полный законный Cleric из кампании.

## Private API: только стенд

В `tests/support.py` используются `_get_live`, `_emit`, `_sync_legendary_resistance`,
inspection полного `live.rng` и установка валидированного trait/resource fixture
для LR. Это позволяет изолировать damage→save fold, повторное согласование и
состояние RNG. LR-trait у такого механического fixture не означает, что настоящий
Cleric имеет Legendary Resistance.

Эти private вызовы **не предлагаются Solo-хосту** и не входят в публичный API
адаптера. Cast/recast/attack/drop при этом проходят через публичные Native intents.
Conftest отказывает запуску, если `dnd5e_engine` или `dnd5e_srd_data` импортирован
не из указанной экспериментальной копии. В тестах запрещены внешние соединения;
loopback допускается для Windows asyncio wakeup socket.

## Воспроизведение

Из корня SoloRealmsAI, существующим локальным окружением, без установки пакетов:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.venv/Scripts/python.exe -X utf8 -B reports/nat20-upstream-repair/prepare.py --name trial-03
.venv/Scripts/python.exe -X utf8 -B reports/nat20-upstream-repair/run.py --name trial-03 --attempt run-01
```

Либо повторить только проверки сохранённой исправленной копии, выбрав новый
каталог результата:

```powershell
.venv/Scripts/python.exe -X utf8 -B reports/nat20-upstream-repair/run.py --name trial-02 --attempt run-03
```

`prepare.py` проверяет lock, делает две отдельные копии, генерирует/применяет
patch и фиксирует hashes. `run.py` задаёт PYTHONPATH только на source dirs
выбранной копии и стенд, отключает автозагрузку pytest plugins/bytecode/cache,
запускает только два небольших файла тестов и сравнение traces. Ненулевой код
старого correctness suite допустим только для точного списка 10 ожидаемых
failures; unexpected failures/errors проваливают эксперимент. Каждый прогон
имеет собственные JUnit/log/summary и pytest-temp; существующие каталоги не удаляются.

## Ограничения и следующие gates

- Это узкий эксперимент, не production patch/release и не полный upstream suite.
  Ни engine pins, ни package metadata рабочего проекта не обновлялись.
- Upsert проверен на refresh одного шаблона Bless и scalar effect values со
  стабильными/пустыми statuses. Произвольная смена statuses/ownership при той же
  identity и исправление уже повреждённых seeded effects не сертифицированы.
- Не проверены все условия/advantage producers, реакционные цепочки,
  произвольные multiclass/monster spells и каждый путь отсутствующего combatant.
- Не решены законность полного Cleric, полный V/S/M gate и внешний effect clock.
  Post-combat time и перенос длительности остаются отдельными блокерами.
- Исправление меняет code hash и RNG после effect-aware save. Старые replay
  должны оставаться на прежнем runtime. Здесь не реализован multi-runtime router,
  migration, упаковка Windows или новая политика fingerprints.
- Снятие pending у Bless требует отдельного разрешённого этапа и всех прежних
  gates. Успех этого эксперимента такого разрешения не даёт.
