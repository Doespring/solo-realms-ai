# Экспериментальный адаптер готового Nat20 — prototype-1

Реализован Python API из `PROTOTYPE_INTEGRATION.md`: catalogue search/detail,
нативный build schema/preview, создание экспериментальной кампании, capabilities,
общие команды/снимки и восстановление. UI, Game/Narrator, память, cloud и packaging
этим пакетом не редактируются. Native runtime остаётся Nat20 0.7.0,
commit `e187c813bb4e43c4e4e62942ecd83831f1cdbcaa`, без fork/patch.
Новый experimental fingerprint:
`95ccb98deeca8009aa02ef77069d3d9aa112e4e813954e98be888e7f583640aa`.

## Что работает

- Новый явный release `prototype-srd-2024-v1`: все 1 663 закреплённых canonical
  JSON из `dnd5e-srd-data`, включая 12 классов, 14 видов, 339 заклинаний,
  341 монстра, 546 предметов. Все документы имеют provenance SRD 5.2;
  это данные конкретного Nat20 pin, **не** локальный импорт Foundry 5.3.3.
  Manifest проверяет каждый SHA-256 по неизменённому `vendor/nat20/UPSTREAM.json`.
- Старый минимальный release, его manifest и fingerprint
  `374afd1a3898e089e023130cc342a0f3c6fe88a75bf4f8ee7e3bf865852abf75`
  сохранены. Старые fighter-рецепты не мигрируют. Один runtime, разные данные.
- Создание принимает настоящий `CharacterBuildSpec`, произвольную выбранную
  экипировку/вид/класс и допустимые upstream choices. Производные значения,
  features, spell grants и ресурсы берёт только native builder. Встроенного
  нового жёстко заданного персонажа или вручную добавленных классовых свойств нет.
- Встречи — типизированная карта, позиции, до 20 участников с разными monster
  slug; в кампании до 64 авторских встреч. HP/AC/сопротивления/действия берутся
  из canonical и native hydration. XP — явный авторский бюджет, default 0;
  распределяет Nat20, записывает прежний RewardReceipt.
- Проверки/спасброски выбираются по ID из состояния. Если авторские checks не
  переданы, доступны 18 навыков, 6 характеристик и 6 saves без выдуманной Сл.
  Сл/преимущество/помеха — данные сцены, не аргументы LLM. Expertise/Jack of All
  Trades/Reliable Talent передаются существующему `resolve_check`, без формул Solo.
- `native_action` отображает закрытый набор всех 19 публичных PlayerIntent
  операций и их фактические reference/choice поля. Можно выбрать оружие,
  действие, особенность/activity, предмет, заклинание/цели/ячейку, клетку/направление,
  grip/bonus-action choice, points/charges. Нет урона, stat override, Сл, кода
  или state patch от AI. Referenced equipment/features/spells проверяются по
  сохранённому build/derived sheet/prepared set, остальное проверяет Nat20.
- Штатные `end_turn`/NPC policy, отдых, journal/memory/ACL, идемпотентность,
  версии и транзакционный rollback сохранены. Replay проверяет новый полный
  native read-model, ресурсы, HP, инициативу и события до продолжения.
- Серверная проекция предлагает ID встреч из кампании и общие native кнопки;
  UI может строить прочие формы из NativeAction schema/catalogue/capabilities.

## Конкретный разрыв NPC: не скрыт под успешным тестом

`EncounterMemberSpec.attack_bonus` у pinned Nat20 — числовое поле с default 0.
Canonical monster не содержит вычисленный to-hit; upstream monster context
использует этот числовой override. Реальный первый прогон Wolf дал `modifier=0`.
Это не объявляется корректной атакой D&D. Минимальное доказательство сохранено
в `reports/prototype-1/npc-to-hit-gap.json`.

Actor может получить `attack_bonus` вместе с `attack_bonus_source` из доверенных
авторских входов кампании; адаптер лишь передаёт их в родной spec. В тестовой
авторской встрече Wolf получает +4, Rat +2 с явным authored source, **не** как
универсальная вычисленная таблица SRD. Если binding отсутствует, начало встречи,
движение/таргетирование работают, но попытка NPC attack вызывает явный отказ
и полный откат всей команды. Ошибка отмечена в каталоге/capabilities. Автоматическое
получение этого недостающего значения из Foundry ещё требует реализации у источника;
ручная формула/изменение Native runtime не добавлены. UI, который задаёт только
monster slug, должен показывать этот blocker, а не обещать полный обычный бой.

## Проверки

`tests/test_nat20_prototype.py`: 6 passed в последнем smoke (83.61 s).
Воин, плут и волшебник созданы builder из обычных значений, выполнили разные
команды в двухцелевой встрече, сохранились и продолжили в новом Python-процессе
с теми же событиями/состоянием. Magic Missile действительно нанёс три порции Force
damage по выбранным целям и потратил одну ячейку; завершение боя/длительный отдых
восстановили slots, следующая встреча началась. Зелье реально расходует нативный
counter, повтор отклоняется; авторский Wolf реально бросает с переданным +4.
Недостающий to-hit, Bless, Hide, не принадлежащее оружие, лишние поля и stale
запросы отклоняются; ошибка памяти откатывается; old preview.7 fixture сохранён.

Отдельно прошли strict tool allowlist и прежний closed-contract/old-fixture test.
Всего 8 уникальных целевых проверок по последним результатам разных прогонов;
источники — `reports/prototype-1/{bound,items,final}.xml`. Полный набор/EXE
не запускались; общий regression остаётся интегратору.

Это проверка Python backend. Отдельный интеграционный прогон другого чата
обнаружил отсутствие `native_action` в серверном словаре подписей старого
`playable_ui.py` (HTTP 404 до вызова движка). Этот UI-owned файл не изменялся
здесь; blocker и воспроизведение сохранены в общем `PROTOTYPE_INTEGRATION.md`.
Не объявляем все HTTP-кнопки/общую сборку принятыми на основании этого smoke.

## Границы и дальнейшее портирование

Каталог — полный справочник **SRD pin**, а не полный PHB/DMG/MM и не список
индивидуально доказанных механик. `native_candidate/native_experimental` не
означает поведенческую проверку каждой записи. Unsupported/no-effect/_failed
операции отказывают без частичного commit. Известный неверный Bless и бесплатный
Hide заблокированы отдельно; остальная магия/класс целиком не отключаются.

Native builder неполно проверяет preparation counts/class spell lists и все
feat/class choice prerequisites. Prepared selection задаётся явно при создании;
мартиальный build без native spell grant не получает произвольный cantrip.
Полная V/S/M/free-hand/material accounting в Native не реализована; metadata
не выдаётся за соблюдение этих ограничений. Заклинания/предметы здесь исполняются
через активный бой; отдельный out-of-combat cast/ritual path не добавлен.
Проверки/отдых с timed effects, mixed-class Hit Dice pools, death saves/0 HP,
отложенные legacy world/adventure механики и восстановление зарядов всех предметов
остаются отдельными неподключёнными путями. Лимит replay 256 сохранён.

Два кратких первых harness-прогона повторены после изменения проверяемого binding;
старые XML не переименованы в якобы финальные. JSON summary указывает текущие
доказательства, release и файлы. Исходные data/ключи/incident evidence не открывались.

Очистка: собственные synthetic DB и временные pytest-профили удалены;
остаются исходники, XML/summary и минимальное доказательство to-hit gap.
