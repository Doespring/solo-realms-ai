# Независимый аудит экспериментального Nat20 patch

18.10.2026, Europe/Samara. **Вердикт: FAIL текущей версии как общего upstream
патча.** Три узких исправления имеют содержательные положительные доказательства,
но глобальный upsert оставляет устаревшее владение статусом. Это подтверждённый
дефект за пределами исходных 16 correctness cases, а не опровержение их результатов.
Только исследовательское использование; adoption и активация Bless заблокированы.

Проверен неизменённый `nat20-three-defects.patch`, SHA256:
`122c7d88e4c35fd98ac6fa74d57a975d8c47828b9ee95c38055dd9e0a6374ff7`.
Основа — Nat20 0.7.0, commit `e187c813bb4e43c4e4e62942ecd83831f1cdbcaa`.
Оригинальные patch/tests/fixtures, vendor, установленный runtime, pins, production
и Preview9 не редактировались. Ничего не опубликовано/установлено/собрано,
платных вызовов 0.

## Что проверено чтением

| Область | Оценка |
|---|---|
| Alias | Один shared alias Foundry save key → `save.bonus`; исходные changes сохраняются, priority/mode обрабатывает прежний resolver. Inline combat alias заменён эквивалентным вызовом. Четыре attack keys не объединяются этим helper — верно для отдельной логики sibling bonuses |
| Concentration save | Переход на существующий `roll_save` использует реальные save modifiers, exhaustion и bonus dice из hydration. Порядок d20→d4 сохранён; caller выдаёт один `ConcentrationCheck`, без второго `SaveRolled`. Нет второго расчёта d4 в хосте |
| LR | Primitive изменяет одноразовые sidecars; событие LR следует после проверки, затем прежний sync обновляет authoritative pool и armed count. Event-index reconciliation предотвращает повторное списание. Пороговые Bless/Bane-тесты проверяют именно смену решения о расходе, а не только положительный исход |
| Upsert | Новая точная identity выигрывает, unrelated identities сохраняют порядок; target/origin не сливаются. Но обновляется только список effects. Старые `ActiveCondition`/coarse conditions и ownership при изменении statuses не согласуются. Глобальная функция охватывает все эффекты, не только неизменный Blessed |
| Refresh времени | Дополнительный публичный recast-тест прошёл: новый срок переживает старый момент expiry и затем завершается один раз. Это подтверждение конкретного Bless, не общего refresh всех индексов |

Ключевые места в проверенной копии: `rules/effects.py:60–78,196–210`;
`orchestrator.py:712–748` (LR sync), `:5184–5227` (новый save),
`:5373–5468` (upsert/conditions), `:5471–5517` (expiry).

## Подтверждённый блокер: статус переживает собственный источник

Три новых адресных probe-теста размещены отдельно в `independent/test_review.py`.
Оригинальные тесты не менялись; запуск — на новых `independent/copy-fixed` и
`copy-pinned`, с запретом внешних соединений и проверкой путей импорта.
**Результат: 1 failed, 2 passed, 2 предупреждения, 5,52 с.**
Полный suite и исходные 16 cases повторно не запускались.

Валидный контрпример:

1. A с identity `(hero,refresh,trusted:own)` накладывает Poisoned.
2. Независимый B также накладывает Poisoned.
3. Новый A с той же identity и пустыми statuses заменяет старый A.
4. B истекает.
5. В effects остаётся только новый A со `statuses=[]`, но в actor.conditions
   остаётся Poisoned с `source_effect_id=refresh`; coarse active_conditions тоже
   содержит Poisoned.

Список эффектов уже заменил A, а ownership его прежнего состояния пережил замену.
Expiry B сохраняет запись, потому что ID A всё ещё есть в remaining effects,
не проверяя, владеет ли новая версия A этим статусом. По коду expiry самого пустого
A также не зайдёт в ветку очистки statuses. Последнее — вывод из исходника,
не дополнительный четвёртый тест.

Это не ошибка JSON или выдуманная игровая механика: тест подаёт существующие
типизированные EffectApplied/ConditionApplied/EffectExpired. Private emit используется
только как стендовый ввод fold, как и в исходном эксперименте, не предлагается Solo.
На старом append-поведении старый A оставался активным; новый upsert удаляет его
из effects, создавая рассогласование нового состояния с оставшимся условием.

Доказательства: [вывод трёх probes](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent/output.txt),
[остаточное состояние](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent/status-refresh.json),
[JUnit](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent/junit.xml).
Это ровно непокрытая граница statuses/ownership, честно отмеченная автором RESULTS;
однако сам глобальный upsert этой границей программно не ограничен.

## Два дополнительных PASS

- **Refresh срока через public API:** первый Bless → три своих хода → второй
  Bless → восемь своих ходов: единственный эффект ещё активен; следующий свой
  ход: один EffectExpired(duration), цепочка пуста, ячеек 0. Старый срок не
  снял новую версию раньше. [Результат](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent/duration-refresh.json).
- **No-effect differential:** CON 16, proficiency override 3, владение CON save,
  Exhaustion 2, нейтральная концентрация без bonus changes, armed LR; два
  последовательных damage events, второй доводит HP до 0. Pinned и fixed
  полностью совпали по всем событиям этих шагов, HP/chain, LR, полному hash RNG
  state и четырём следующим draw. Проверки: natural 2/3, modifier 2, totals 4/5;
  LR расход 1/0. Первая неудача превращается в успех, вторая без armed LR остаётся
  неудачей. [Обе трассы](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent/no-effect-extra.json).

Это один дополнительный differential case, а не универсальное доказательство
неизменности RNG. Четыре исходные no-effect traces покрывают два seed при CON+2,
DC10, damage1, без proficiency/exhaustion и с/без LR; их одинаковость значима,
но не охватывает все advantage sources, реакции и вложенные resolutions.

## Насколько независимы исходные expectations

Сохранённые XML подтверждают: old correctness 6/16 pass и 10 failures, fixed
16/16 pass; skips отсутствуют. Эти наборы я прочитал, а не перезапускал.

- Concentration oracle использует отдельный seeded Random, явные d20 и signed
  d4, total и полный RNG state. Ожидание не берётся из исправленной функции;
  old failures дополнительно показывают чувствительность теста к дефекту.
- LR assertions проверяют число/порядок событий, фактический remaining pool,
  armed count и два перекрывающихся sync. Пороговые seeds действительно
  меняют решение о LR. LR на Cleric — объявленный механический fixture,
  не доказательство законности игрового класса.
- Alias-тест сравнивает два входа одного resolver: Foundry key и прежний
  canonical key. Это корректный тест эквивалентности маршрутизации, но не
  независимая сертификация всей арифметики/modes. Проверка nonempty breakdown,
  RNG и немутирования входа полезна. Нужны дополнительные mode/priority,
  disabled/scope и mixed-key cases перед общим принятием.
- Recast-тест надёжно ловит две активные копии и остаток после drop. Его
  `1 <= attack bonus <= 4` слабее точного oracle: сам по себе такой диапазон
  допускает неверный фиксированный бонус и не доказывает число RNG draws.
  Перед adoption нужны точный seeded бонус и RNG state после атаки.
- Identity/order-тест проверяет scalar changes и разделение target/origin,
  но не смену statuses, ownership или все sidecars. Поэтому 16/16 не является
  самоподтверждающейся фикцией, но оставляет обнаруженную дыру покрытия.
- Harness считает passed как tests−failures−errors, без вычитания skips;
  сейчас skips=0, цифры достоверны. Перед будущим CI gate следует явно
  отклонять skip/xfail и сверять точные node IDs, не только failure prefixes.

## Риски, ещё не проверенные исполнением

1. Согласованность refresh с `conditions_by_effect`, concentration ownership,
   repeat-save и duration sidecars при изменении flags/источника. Нельзя просто
   удалить все условия: другой эффект или независимая запись могут ими владеть.
   Dedupe всей target-коллекции также может убрать старые дубли других identities
   без отдельных expiry events; исправление повреждённых seeded states не доказано.
2. Новая save-ветка может бросить исключение из bonus parsing/context hydration.
   В `_emit_apply_damage` HP записывается до неё (`:5148`), а
   `save_primitive._passive_save_bonus` имеет ValueError на неразбираемом выражении.
   Fallback отсутствующего combatant не покрывает этот путь. Нужны invalid-data
   и атомарность/rollback tests; в этом аудите четвёртый тест не запускался.
3. Несколько целей/кастеров, condition-specific advantage, exhausted/unarmed LR,
   reentrant damage/reaction resolutions и концентрация при incapacitation
   требуют отдельного покрытия. Дополнительный no-effect PASS не закрывает их.

## До любой adoption

1. Исправить или явно ограничить глобальный refresh-контракт. Согласованно обновлять
   все принадлежащие старой identity статусы/индексы, сохраняя чужое владение.
   Закрыть обнаруженный counterexample. Независимые alias/concentration изменения
   можно пересматривать отдельно от небезопасного общего upsert.
2. Дополнить точные semantic/RNG/LR/ownership/error-path tests и провести отдельно
   разрешённую upstream/Solo-регрессию. Не подгонять expectations под текущий patch
   и не превращать потерю статуса/слота в успех.
3. Новый code hash требует отдельного runtime release/pin и правил выбора runtime
   для старых replay. Сохранить прежний immutable runtime; не переписывать digest
   старых кампаний и не ослаблять проверку pinned кода. Windows packaging/restart
   и миграция этим экспериментом не реализованы.
4. Даже исправленный engine не закрывает прежние Bless gates: полный законный
   Cleric, V/S/M, post-combat clock и остальные ограничения остаются pending.
   Публикация/обновление runtime/активация требуют отдельного задания.

## Сохранность

[preservation.json](C:/Users/iwa41/Documents/Codex/2026-10-13/files-pasted-by-the-user-solo/outputs/SoloRealmsAI/reports/nat20-upstream-repair/independent/preservation.json): проверены **4001**
защищённый файл; изменений 0. Обе reviewer-копии побайтно совпали с исходными
экспериментальными копиями после тестов. Исходные patch/tests/fixtures и результаты
trial-02 сохранены. Новые файлы — только данный отчёт и отдельный каталог
`independent/` с reviewer-копиями, тремя probes и доказательствами.
