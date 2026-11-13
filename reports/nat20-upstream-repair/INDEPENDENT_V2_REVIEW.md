# Независимый review V2 alias/concentration

18.10.2026, Europe/Samara. **Вердикт: FAIL текущего alias/concentration candidate.**
Причина — новая зависимость RNG концентрации от постороннего AC-sidecar;
дополнительно подтверждён незакрытый error/atomicity gate. Это не оценка refresh:
upsert в V2 отсутствует, recast/refresh остаются pending.

Проверен только `v2/nat20-alias-concentration-v2.patch`, SHA256
`85022d9bbf72badb5726d25ebea72c84fcc1abde572b123236076ba5659a2b35`.
Ни отклонённый refresh patch, ни старый FAIL-отчёт не изменялись.
Никакого adoption, установки, pin/Solo/runtime правок, публикации или сборки.

## Scope проверен независимо

Diff между pinned и candidate копиями заново получен из файлов и **в точности
совпал** с указанным patch. Изменены только `rules/effects.py` и `orchestrator.py`.
Тела шести функций совпадают с pinned по исходному тексту:
apply effect, expire effect, lifecycle links, concentration writeback/drop,
seed active effects. Dedupe/upsert в кандидат не вернулся.
[Проверка scope](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent-v2/scope.json).

Alias ограничен одним save key, сохраняет исходные changes и прежние mode/priority
folds; attack siblings не объединяются этим helper. LR по-прежнему использует
throwaway sidecars и существующий идемпотентный sync по индексу события.
В этой части нового обхода hash/validation или второго расчёта кубиков не найдено.

Сохранённые XML прочитаны, исходные suites не перезапускались: V2 old16 —
14 pass / 2 fail; refresh8 — 3 pass / 5 fail; errors/skips отсутствуют.
Два красных старых recast/order теста ожидаемо подтверждают отсутствие upsert.
Их я **не считаю новой регрессией V2**. Runner=0 означает совпадение матрицы,
а не исправление refresh. Четыре сохранённые pinned/V2 no-effect трассы также
заново сравнены как JSON: совпадение точное.

## Три новых адресных probes

Созданы только отдельные `independent-v2/copy-candidate` / `copy-pinned` и
`test_review_v2.py`. Импорты проверяются на принадлежность этим копиям, bytecode/
plugin autoload отключены, внешняя сеть запрещена. **2 failed, 1 passed,
2 предупреждения, 7,40 с.** Полный suite не запускался.

| Probe | Результат |
|---|---|
| Неверная save-формула при настоящем public monster turn | FAIL atomicity gate: ValueError после HP 10→9 и продвижения RNG |
| Отсутствующий combatant, сохранённые tracked HP/concentration chain | PASS: прежний defensive fallback без исключения; events/HP/chain/RNG и следующие draw совпали |
| Нейтральная концентрация героя + AC dice bonus только у foe | FAIL: лишний AC draw до CON d20; события и RNG отличаются от pinned |

[Вывод](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent-v2/output.txt) ·
[JUnit](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent-v2/junit.xml).

## P1: несвязанный AC sidecar меняет save и RNG

Валидный механический fixture: герой концентрируется на neutral effect без
save changes; у другого combatant имеется поддержанный `ac.bonus = 1d4`.
Прямой типизированный DamageApplied(amount=1) не является атакой и не требует
расчёта AC. Seed 2, CON modifier +2.

| Наблюдение | Pinned | V2 |
|---|---|---|
| CON natural / total | 2 / 4 | 3 / 5 |
| Следующие четыре d20 draw | 3, 3, 12, 6 | 3, 12, 6, 10 |
| RNG state hash | `8f3541d4…b989` | `d5e23d51…3247` |

Источник: новый concentration path в `orchestrator.py:5187–5203` вызывает
**общий** `build_activity_context` на полном hydration payload. Неизменённый
`activities/build_context.py:411–413` материализует каждый passive_ac_bonus
через `roll_expr(ac_bonus,rng)`, включая AC постороннего участника. Затем
вызывается `roll_save`. Это лишний draw ещё до natural d20.

Здесь не требуется сохранять старый RNG при добавлении реального Bless/Bane d4:
такой дополнительный draw необходим. В этом probe **save-бонуса вообще нет**,
а AC другого участника не участвует в проверке концентрации. Поэтому отличие
показывает побочное вычисление нового caller, не желаемый эффект исправления.
Fixture не заявляет новое SRD-заклинание: он использует уже поддержанный
типизированный числовой AC-sidecar. Исходные четыре no-effect tests не имели
такого sidecar и эту границу не покрывают.

[Обе полные трассы](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent-v2/ac-traces.json).
Перед принятием нужно исключить неспасательные RNG-вычисления из concentration
context, сохранив прежний save primitive и нужные save/LR/exhaustion поля.
Ручной reroll/коррекция результата в Solo не является исправлением.

## Error/atomicity gate: теперь подтверждён public API

Второй fixture задаёт схематически допустимый ActiveEffectChange:
`save.bonus = "not-a-die("`. Для детерминированного попадания у механического
foe attack bonus 100 и damage 1d1; это стенд, не законный NPC-профиль.
После hero pass вызван настоящий `advance_monster_turn`, без прямой подмены
HP. Получен `ValueError: Unparseable passive_save_bonus…`.

До вызова HP героя 10, после исключения 9; actor HP тоже 9, RNG изменился,
event count 9→12. Это тот незакрытый путь из первого review: запись HP происходит
до новой hydration/save-ветки; parse ошибки появились на пути damage→concentration.
Probe проверяет требование безопасного отказа, а не объявляет некорректную
формулу поддержанной.

Текущий Solo disposable worker на таком отказе отбрасывает процесс и не должен
фиксировать незавершённый state. **Повреждение пользовательской SQLite здесь не
обнаруживалось и не моделировалось.** Частичная мутация подтверждена только
в изолированном LiveCombat через public API. Для upstream/adoption нужны
валидация до изменения состояния либо проверенный transaction/error boundary;
молчаливый пропуск неизвестной формулы/выдуманный успех недопустимы.

[Before/after и события](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent-v2/invalid-bonus.json).

## Что прошло и предел вывода

Missing-combatant probe явно создаёт повреждённый внутренний fixture удалением
actor из initiative при оставшемся HP/chain. В обеих копиях один fallback
ConcentrationCheck с modifier 0, без ошибки; дальнейшие события/цепочка и полный
RNG hash совпали. Это подтверждение узкого защитного пути, не нормальный gameplay.
[Трассы](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent-v2/missing-traces.json).

Положительные 14 alias/concentration/RNG/LR cases и четыре сохранённых трассы
остаются содержательными доказательствами своих входов. Но их нельзя расширять
до всех sidecars: новый валидный AC-контрпример это опровергает. Никакого нового
refresh correctness PASS этим review не выдаётся.

## До отдельного runtime release

1. Закрыть побочное потребление AC RNG в concentration path, затем проверить
   per-target/save sidecars, порядок d20→save bonus и LR без лишних draws.
2. Закрыть invalid bonus/context validation и безопасную обработку исключений;
   доказать отсутствие частичного canonical commit на разрешённых API-путях.
3. Сохранить отсутствие общего upsert и recast pending. Ownership/lifecycle и
   post-combat clock не становятся исправленными от прохождения alias tests.
4. Отдельно разрешить релевантную upstream/Solo-регрессию и runtime release
   по `docs/NAT20_RUNTIME_RELEASE_PLAN.md`: собственный code hash/pin,
   независимый content binding, старый immutable runtime для старых recipes,
   fail-closed неизвестные пары, новый EXE/старый save/restart. Не переписывать
   прежние fingerprints. Законный Cleric/V/S/M и activation gates остаются отдельно.

## Сохранность и параллельное изменение

Проверены 4024 защищённых файла. Patch V1/V2, прежний FAIL-отчёт, оригинальные
tests/fixtures, обе пары экспериментальных runtime copies, installed engine,
pins и Preview9 не изменились. Reviewer-копии также совпали с оригиналами.

**Единственное отличие captured before/after — `web/game-screen.js`.** До аудита
он уже отличался от source Preview9, а за время проверки изменился ещё раз.
Мои действия этот файл не записывали; автор/авторизация внешнего изменения этим
аудитом не установлены, отката не выполнялось. Поэтому безусловное заявление
«весь production snapshot неизменен» здесь было бы неверным. Это не влияет
на три probes: они запускаются из отдельных engine copies без web/Solo imports.
[Preservation](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent-v2/preservation.json) ·
[before/after hashes](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent-v2/concurrent-source-change.json).

Новые файлы этого аудита — данный отчёт и `independent-v2/`. Никакая
механическая правка, dependency install, публикация или Windows-сборка не выполнена.
