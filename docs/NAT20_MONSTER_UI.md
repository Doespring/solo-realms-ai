# Monster attack profile — UI seam

18 октября 2026. **UI READY / source freeze**. Production-владение UI: только `web/prototype.js`.

Engine-контракт из `NAT20_PLAYABLE_NEXT.md` прочитан. `catalog_detail('monsters:'+slug)` возвращает `attack_profile`; текущий `/api/prototype/catalog/{category}` вызывает только `catalog(...)`. HTTP detail route при первом осмотре отсутствует.

Владелец API добавил `GET /api/prototype/catalog/{category}/{slug}` → существующий `Nat20GameEngine.catalog_detail(category+':'+slug)`. UI использует exact route `/api/prototype/catalog/monsters/{slug}`. Backend здесь не редактировался; реальный smoke выполнен уже с подключённым маршрутом.

## Изменение

- `drawBonuses` получает и кэширует в текущей сессии **только выбранные** профили. Список из 341 записи не обрабатывается заранее. Снятый выбор не получает устаревший ответ в новый UI.
- `native_derived`: видны готовый бонус, метод и SHA источника Native. Ручных полей нет. Payload исключает старый локальный override для такого противника — включая сохранённый ранее явный `0`.
- `unbound`: показана точная серверная причина. Существующие поля числа/источника остаются **необязательным** авторским override. Пустые поля не превращаются в ноль; явно введённый ноль сохраняется. Непредставимость конкретной атаки не блокирует весь класс или магию.
- Повтор проверки при ошибке detail доступен отдельной кнопкой. Пока профиль загружается, старт ждёт ответ; sheet preview не зависит от этой read-only проекции. Отобранные overrides передаются только для выбранных unbound-профилей.
- UI не рассчитывает характеристики, бонусы, урон или законность. Существующие сохранённые определения и `.3`/frozen Drive не менялись.

## Единственный новый smoke

`tests/test_monster_profile_ui.py`: **1 passed**, **24.09 с**. Настоящие зарегистрированные API, pinned Nat20, headless Edge **1280×800**, MockLLM, новый собственный профиль; внешние соединения заблокированы, `SOLO_TEST=1` до app import. Endpoint/game-response fixtures — **нет**, реальных AI-вызовов — **0**.

1. Выбран Wolf: реальный detail вернул `native_derived`, **+4**, `native-stat-block-v1` и SHA. UI показал их без ручного поля.
2. Выбран Knight: реальный локальный detail вернул `unbound` — один scalar не представляет модификаторы разных действий `[2,5]`. Причина показана буквально; необязательные поля пусты, после Native preview старт доступен. Явный авторский `0` и источник сохраняются в черновике.
3. Knight снят, создана обычная Rogue/human кампания с Wolf. Payload содержит только Wolf и пустые `encounter_attack_bonuses`/`encounter_attack_sources`, несмотря на старый локальный override Wolf=0. Python host проверил настоящий сохранённый actor: **attack_bonus=4**, source.kind=`native_derived`; memory.audit чистый.
4. За всё взаимодействие detail GET выполнен ровно для **wolf, knight**; повторный render не запросил их заново. JavaScript ошибок **0**. Сервер/браузер завершены; подходящих собственных Python/Node процессов **0**. Оба снимка просмотрены.

Результат: [trace](../reports/monster-profile-ui/results.json), [Wolf](../reports/monster-profile-ui/native-wolf.png), [unbound Knight](../reports/monster-profile-ui/local-unbound.png). `git diff --check` — без ошибок. Собственный прежний `test_prototype_ui.cjs` обновлён под Native Rat вместо обязательного ручного бонуса, но заново не запускался. Два PDF UI-прогона и семь engine smoke переиспользованы; full/EXE/прочих прогонов не было.

## Границы и передача

Сборке/root: web-файл **frozen**, новый targeted **1 passed / 24.09s**. Текущий HTTP detail route принадлежит Сборке; общий INTEGRATION здесь не менялся. Auto-binding to-hit не является гарантией всех rider/damage/spell эффектов Native. Причина unbound пока сохраняет техническую формулировку Native в существующем дополнительном блоке.

Изменённые собственные файлы этапа: `web/prototype.js`, `tests/test_monster_profile_ui.py`, `tests/test_monster_profile_ui.cjs`, одно ожидание собственного `tests/test_prototype_ui.cjs`, этот документ. Подготовка PDF, engine, память, app и чужие файлы не редактировались.

Очистка: процессы завершены; тесты, trace и снимки сохранены. Остался единственный новый изолированный синтетический стенд `reports/monster-profile-ui/tmp-smoke` для согласованной очистки. По текущему указанию не повторять policy-отказы новая попытка удаления не выполнялась; прежние отклонения не обходились.
