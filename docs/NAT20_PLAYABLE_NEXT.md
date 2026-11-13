# Native playable next — фактический стык

Владелец engine: Правила. UI/app/Game/preparation/memory и общий integration
здесь не редактируются. Один прежний pinned Nat20 runtime и прежние releases.

Новые кампании больше не требуют ручного attack_bonus для обычного профиля:

```python
api.create_experimental_campaign(name, build=build, encounters={
    'forest': {'grid': {'width': 10, 'height': 8},
               'hero_position': {'x': 1, 'y': 1, 'z': 0},
               'actors': [{'id': 'wolf', 'monster': 'wolf',
                           'position': {'x': 3, 'y': 1, 'z': 0}}]}})
Nat20GameEngine.monster_attack_profile('wolf')
```

`monster_attack_profile(slug)` возвращает `monster,monster_sha256,method,
attack_bonus,status,reason,activities`. `method='native-stat-block-v1'`,
status `native_derived` или `unbound`. Activities содержат action/activity/weapon
и modifier. Это локальная read-only проекция, не бой и не игровой бросок.

UI больше не должен требовать `attack_bonus` и `attack_bonus_source`: оба поля
можно не передавать. Также допустимы оба `null`; `0` с авторским source — явный
override, а не запрос автоматического расчёта. Не подставляйте ноль вместо
отсутствующего значения. Только выбранные профили разрешаются при создании кампании,
`monster_attack_profile(slug)` или `catalog_detail('monsters:'+slug)`; список
каталога/options/capabilities не запускает расчёт 341 профиля. Detail возвращает
дополнительный `attack_profile`; непредставимость видна там в known_issue.
Результат кэшируется по slug/SHA pinned canonical. Ни игровой ход, ни журнал,
ни ресурсы, ни campaign RNG в проекции не участвуют.

Она вызывает публичные `StatBlockMagnitudes`, `build_activity_context` и
`resolve_attack` на canonical статах/оружии с отдельными disposable actors,
events/RNG. Число берётся из Native AttackRolled.modifier; формулы D&D в Solo
нет. Упрощённый native EncounterMemberSpec принимает один scalar, поэтому
несовпадающие модификаторы разных действий и nonliteral attack bonus не
объявляются единым работающим профилем. Причина видна в catalogue known_issue.

При новом создании успешная проекция автоматически заполняет существующие
`actor.attack_bonus/attack_bonus_source`, `source.kind='native_derived'` и SHA
canonical документа. Игрок не вводит число. Доверенные явные авторские overrides
остаются совместимыми. Существующие сохранённые определения/bonus/replay не
переписываются. Непредставимый профиль остаётся unbound и при попытке удара
получает прежний атомарный отказ — это не запрет всей встречи/магии/класса.

`native_action` остаётся закрытым DTO с 19 public intent operations. Убрана
дублирующая статическая проверка features адаптера: repertoire/grants проверяет
сам Native. Spell selection проверяется по public live spells_known_by_entity,
включая engine-granted spells. Ownership экипировки, targets, versions, rollback,
Bless/Hide defect guards сохранены. Отдых/восстановление зарядов этим пакетом
не расширялись. Старый минимальный release не получает новых операций/данных неявно.

Native legacy monster damage/context всё ещё имеет upstream approximation;
автоматическое связывание to-hit не является сертификацией каждого rider,
damage formula, spell или всего D&D. Catalogue остаётся native_candidate.
Public spell/item operations этого адаптера по-прежнему требуют активный бой;
OOC универсального cast-handler в этом изменении не придумывается.

## Результат коротких smoke

7 уникальных целевых проверок прошли по последнему результату каждой:
`smoke.xml` — проекция Wolf/Rat/Goblin и отказ Knight; `abilities.xml` — Wizard;
`monk.xml` — Monk; `guards.xml` — ленивый каталог, отдельный отказ Rage с
продолжением игры Barbarian, rollback памяти/stale/старый fighter fixture.
Это разные короткие прогоны, не полный suite и не повтор прежних 60 тестов.

Monk второго уровня с обычными значениями использовал Patient Defense через
Monk’s Focus, реально потратил 1 Focus Point и сохранил Native dodging status;
Wolf реально бросил с полученным от Native +4. Wizard первого уровня потратил
ячейку Magic Missile и победил Wolf. Для обоих save → новый Python-процесс →
replay/continue совпал с непрерывным выполнением по событиям и полному состоянию.
Проекция не создаёт journal event и не меняет global/campaign RNG.

Исправлена только проекция тактических статусов: Native `dodging/disengaging`
не являются Literal conditions старого Hero. Они остаются в Native state и
общем snapshot/effects, а read-model Hero получает лишь разрешённые его
существующим DTO условия. Проверки механики и pair(event,snapshot) не ослаблены.

Дополнительно выявлен реальный Native дефект Rage: EffectApplied несёт
`disabled=true`. Активация этого feature явно заблокирована с откатом, а не
объявлена успешной; Barbarian сохраняет другие действия. Bless/Hide остаются
отдельными известными defects. Остальная магия/класс целиком не отключаются.
Начальные красные XML сохранены: Rage выявил disabled effect, Monk — ошибку
read-model, Wizard — неверное ожидание NPC-удара после уже убитого Wolf.
Указанные последние проверки зелёные; исправленные пути не скрыты удалением защит.

Native runtime/pin/imported manifests не менялись; full/EXE/live/paid не было.
Общий integration-документ остаётся Сборке. Изменённые engine-файлы готовы
для общей интеграции. Итог/хеши и статус очистки —
`reports/nat20-playable-next/results.json`. Старые policy-blocked стенды не тронуты.

Engine-файлы этапа frozen для интегратора. Очистка: автоматическая проверка
отклонила удаление собственного нового стенда (`blocked by policy`); обхода
или другой попытки удаления не было. Остались собственные synthetic profiles
в `reports/nat20-playable-next/{tmp-smoke,tmp-abilities,tmp-monk,tmp-guards}`.
Своих серверов не запускал, процессы тестов завершены. XML, source hashes,
`native-profiles.json` и минимальное `rage-gap.json` сохранены как доказательства.
