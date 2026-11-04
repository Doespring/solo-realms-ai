# V2: разделение патча, общий refresh остаётся неподдержанным

18.10.2026. **Общий upsert не исправлен и не предлагается к принятию.** Использован
прямо разрешённый запасной вариант: независимые alias/concentration изменения
отделены от отклонённого refresh. Все его позитивные ожидания исполняются и
остаются красными. Bless/recast pending; adoption, новый pin и миграция запрещены.

Старые patch/hash/RESULTS/latest, independent review, тесты и failing-доказательства
не переписаны: `v2/immutable-v1.json` и проверка до/после подтверждают равенство
всех прежних файлов. Новые файлы — только этот документ и `v2/`. Рабочий vendor,
installed runtime, pins, Solo adapters/DTO/данные/registry не изменены.
Зависимости, публикации, сборки, платные вызовы и полный suite отсутствуют.
Все копии и результаты сохранены.

## Сначала воспроизведён независимый контрпример

`v2/reproduce_review.py` скопировал **без изменения байтов** оригинальный
`independent/test_review.py` в `v2/reviewer-reproducer/` и запустил только
`test_refresh_relinquishes_old_status_ownership` на прежней V1-копии.
Результат: **1 failed, 2 deselected**, Poisoned остался после expiry B.

Новые доказательства, не перезаписывающие старые:

- `v2/reviewer-reproducer/output.txt`, `junit.xml`;
- `v2/reviewer-reproducer/status-refresh.json`.

Проблема V1 признана: обновление лишь active_effects не освобождает прежнее
владение статусом. Очистить один Poisoned или запретить этот вход недостаточно.

## Почему выбрано разделение

В закреплённом upstream применяются несколько связанных представлений:

- список `ActiveEffect` с identity `(target,id,origin)`;
- `Combatant.conditions` и coarse `active_conditions`; первая структура
  использует `source_effect_id`, без полной origin-identity;
- `conditions_by_effect`, `repeat_save_on_turn_end`;
- `concentration_chain`, countdown и `Combatant.concentration_effect_id`;
- дополнительные expiry/anchor-dependent/reaction/transform связи.

Часть из них обновляет apply/expire fold, часть — последующий
`_record_effect_lifecycle_links` по срезу событий конкретного evaluation.
Refresh с изменением statuses/flags требует согласованного lifecycle-перехода
между этими стадиями. Простая фильтрация actor.conditions не чинит остальные
индексы, а «expire всё» может снять независимое владение или вызвать лишний
cascade. Общий ремонт выходит за безопасную небольшую корректировку списка.

Поэтому V2 **не содержит нового upsert вообще**. Apply/expire/lifecycle primitives
оставлены byte-for-byte как в pinned, проверены тела шести функций:
`_emit_apply_effect_applied`, `_emit_apply_effect_expired`,
`_record_effect_lifecycle_links`, `_writeback_concentration`,
`_drop_concentration`, `_seed_active_effects`.

Ни запрета тестового входа, ни специального case Blessed, ни ручного удаления
условий/индексов не добавлено. Вход принимается прежним append-поведением. Это
сохраняет исходную семантику, **а не реализует правильный refresh**.

## Два новых отдельных артефакта

### Кандидат только alias/concentration

`v2/nat20-alias-concentration-v2.patch`

```text
85022d9bbf72badb5726d25ebea72c84fcc1abde572b123236076ba5659a2b35
```

Два файла: `rules/effects.py` и `orchestrator.py` под
`packages/dnd5e-engine/src/dnd5e_engine/`.
Один общий save-key alias; концентрационная проверка через прежние
hydration/context/`roll_save`; прежнее идемпотентное LR reconciliation.
Никакого dedupe/upsert или изменения ownership-функций. Математика не переносится
в Solo. Дальнейшее принятие этой части всё равно требует отдельного разрешения
и оставшихся upstream validation/atomicity gates.

### Отклонённая часть — только для расследования

`v2/nat20-refresh-UNSUPPORTED-v2.patch`

```text
b4ba194504c066fa5d9c9640232e1a2fe3eeaee28b467d6612171077527cfcab
```

Это выделенный старый небезопасный upsert поверх alias/concentration-кандидата,
**не V2-исправление и не рекомендация применять**. Он нужен для точного разделения
и воспроизведения причины отказа. В новой кандидатной копии он не применялся.
Прежний объединённый патч остаётся неизменным с SHA-256
`122c7d88e4c35fd98ac6fa74d57a975d8c47828b9ee95c38055dd9e0a6374ff7`.

## Прогон без скрытых skip/xfail

Принятый результат классификации границ:
`v2/work/trial-v2-01/run-01/summary.json`, ссылка `v2/latest-v2.json`.
**Это не зелёный общий refresh suite.** Код runner=0 означает только совпадение
явно ожидаемой матрицы, чистоту frozen-файлов и no-effect дифференциала.

| Копия | Все прежние 16 correctness | Новые 8 ownership/refresh checks |
| --- | --- | --- |
| V1 rejected | 16 passed | 4 passed / **4 failed** |
| V2 alias/concentration | 14 passed / **2 failed** | 3 passed / **5 failed** |

Errors, skipped и xfail отсутствуют. Проверяются точные наборы node IDs,
а не prefixes; неожиданный failure/error/skip проваливает проверку матрицы.
Оригинальный файл 16 correctness и его helper не менялись.

### Что прошло у V2

- Все 14 прежних alias/concentration/RNG/LR cases, включая пороговые
  Bless/Bane решения о LR и повторное reconciliation без двойного расхода.
- Четыре no-effect traces pinned ↔ V2 полностью совпали по событиям, публичным
  полям состояния, LR-ресурсам, полному hash RNG и следующим draw.
- Прежние apply/expire сохранили независимый Poisoned-владелец после удаления
  другого; после удаления последнего владельца actor/coarse условия снялись.
- Expiry разделяет target/origin: удаление hero/own не удаляет hero/other или
  одноимённый эффект на foe.
- После reviewer-последовательности в V2 нет нового orphaned ownership:
  старый A по-прежнему активен и действительно владеет Poisoned. **Это не успешный
  refresh**: лишний старый экземпляр остаётся, поэтому позитивный reviewer test
  всё равно падает. Различие явно проверено отдельным coherence-test.

### Что осталось красным

Из прежних 16 на V2: `test_exact_identity_recast_drop` и
`test_identity_scope_and_stable_order`. Они подтверждают отсутствие исправления
общего upsert, а не исключены из запуска.

Новые тесты одинаковы для обеих копий, без version-specific условий:

| Тест | V1 | V2 | Значение |
| --- | --- | --- | --- |
| Reviewer counterexample | FAIL | FAIL | Требуемый refresh не выполнен |
| Освобождение A, сохранение B до его expiry | FAIL | FAIL | Общий ownership-refresh не выполнен |
| Отсутствие orphaned status owner в текущем store | FAIL | PASS | V2 убрал введённое V1 рассогласование возвратом к pinned append, не новым refresh |
| Apply/expire независимых владельцев без refresh | PASS | PASS | Прежний primitive сохранён |
| Независимые target/origin без refresh | PASS | PASS | Прежний scope сохранён |
| Точный seeded attack total и полный RNG после recast | PASS | FAIL | V1 давал один d4; V2 сохраняет неподдержанное дублирование |
| Refresh срока через старую границу expiry, затем один expiry | PASS | FAIL | Не исправлено разделённым патчем |
| Освобождение всех старых lifecycle indexes при смене statuses/flags | FAIL | FAIL | V1 не согласовал условия, repeat-save и concentration связи; V2 их не ремонтирует |

В RNG-тесте выбран промах без damage dice; oracle берётся из сохранённого
предатакового RNG state и делает d20, затем один d4. Проверяются точный total
и полный state, а не только диапазон бонуса. Lifecycle-test использует типизированные
SaveRolled/EffectApplied/ConditionApplied и существующий linkage fold; снимок
индексов сохранён в `lifecycle-indexes.json` соответствующего прогона.

## Воспроизведение и сохранённые копии

Из корня проекта, существующим окружением:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.venv/Scripts/python.exe -X utf8 -B reports/nat20-upstream-repair/v2/run_v2.py --attempt run-02
```

Команда использует сохранённые копии и создаёт **новый** каталог результата.
Для первоначальной подготовки использованы `v2/reproduce_review.py`, затем
`v2/prepare_v2.py`; они намеренно не перезаписывают существующие каталоги.
`prepare_v2.py` применил только alias/concentration через `git apply --check`
и `git apply` к `v2/work/trial-v2-01/alias-concentration/`.

Пути imports проверяются: только выбранная экспериментальная копия engine/data.
Тесты запрещают внешние соединения; Windows asyncio loopback разрешён.
Private `_emit`, `_get_live`, `_sync_legendary_resistance`, linkage fold и доступ
к RNG используются только как стендовые средства; они не предлагаются хосту.

## Неизменные блокеры

Recast gate в Solo остаётся pending и не менялся. Для общего refresh нужен
отдельно разрешённый согласованный lifecycle/ownership ремонт, включая все
используемые индексы и malformed/error paths. Здесь не исправляются damaged
seeded states и не сертифицируются произвольные effect combinations.
Валидация bonus/context и атомарность исключения после записи HP из независимого
аудита также не объявляются закрытыми. Законный Cleric, V/S/M, post-combat clock,
multi-runtime pin/replay и Windows packaging остаются прежними отдельными gates.

Никакая часть V2 не является разрешением на adoption, публикацию, замену runtime,
изменение lock или миграцию пользовательских кампаний.
