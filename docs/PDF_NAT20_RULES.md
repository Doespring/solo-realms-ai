# Rules handoff: явный мир без встреч

18.10.2026. Узкий engine blocker закрыт. Общий `PROTOTYPE_INTEGRATION.md`
не редактировался в этом этапе: его обновляет Сборка со ссылкой сюда.

Фактический вызов остаётся прежним:

```python
api.create_experimental_campaign(name, build=build, encounters={}, ...)
```

`encounters` — обязательный явный словарь, теперь длиной 0..64 в
`PrototypeDefinition`. Пустая карта сохраняется пустой; builder не подставляет
встречу/врага. `capabilities(...).encounters == []`, серверная проекция не
предлагает start_combat. Проверки, saves, отдых и остальной прежний API доступны.
Неизвестный encounter_id получает прежний явный отказ без записи состояния,
события, request или памяти. Ни одного нового механического маршрута не добавлено.

У непустого `PrototypeEncounter` по-прежнему обязательны 1..20 actors,
уникальные допустимые позиции/ID, поверхность z=0, валидный monster reference
при запуске и парный attack_bonus/source при явном Native binding. Изменена
только нижняя граница карты кампании; runtime/pin/арифметика не менялись.
Синхронизированы `prototype-campaign-v1.schema.json` и `nat20-state-v1.schema.json`.
Новых defaults/полей/версий состояния и неявной миграции старых кампаний нет.
Полный каталог и магический command path не изменялись и заново целиком
не сертифицировались.

Проверки: **3 passed, 67.18 s**, `reports/prototype-1-empty-world/tests.xml`:

- `test_nat20_empty_encounters.py`: реальный Rogue → 0 встреч → native check →
  checkpoint → новый Python-процесс → restore → новый native check;
  неизвестная встреча без мутации; отсутствие фиктивной кнопки боя.
- Там же: непустой профиль реально start_combat → native Dash → replay;
  пустой actor list, позиция вне карты и непарный bonus/source отвергаются.
- Прежний `test_atomicity_and_old_fighter_release_survive`: rollback памяти,
  stale version и старый preview.7 fighter save/replay остаются рабочими.

Начальный запуск остановился на setup: отсутствовала родительская папка нового
pytest-стенда. Родитель создан, все три проверки повторены успешно;
`setup-error.xml` оставлен как история, engine-ошибок не выявлено.
SOLO_TEST задан до импортов, базы только в собственном новом синтетическом
профиле. app/preparation/web, исходные data/DB/WAL, ZIP и incident evidence
не трогались. Full/build/paid/live AI не запускались.

Engine-файлы после этого изменения заморожены для общей интеграции.
Очистка: автоматическая проверка отклонила удаление собственного стенда с причиной
`blocked by policy`. Другой shell/способ удаления не применялся. Остался только
собственный временный профиль
`C:\Users\iwa41\Documents\Codex\2026-10-13\files-pasted-by-the-user-solo\outputs\SoloRealmsAI\reports\prototype-1-empty-world\tmp-run`;
XML и `results.json` сохранены как результаты проверки. Своих серверов не запускал;
тестовый процесс завершён. Родительский каталог отчётов не считается мусором.
