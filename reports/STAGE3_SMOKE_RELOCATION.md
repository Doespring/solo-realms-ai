# Этап 3 — перенос smoke helpers

18 октября 2026. Узкая задача владельца QA-helpers; cloud, game/prompts этапа 2,
Native rules, routing builder и cache памяти этим чатом не менялись.

## Перенос / подключение

| Прежний путь (удалён) | Новый путь |
| --- | --- |
| solo/command_smoke.py | scripts/command_smoke.py |
| solo/desktop_cloud_smoke.py | scripts/desktop_cloud_smoke.py |
| solo/desktop_creation_smoke.py | scripts/desktop_creation_smoke.py |
| solo/smoke_fixture.py | scripts/smoke_fixture.py |

Добавлен минимальный scripts/__init__.py. В helpers изменены только пути импортов
из solo и CLI-docstring command_smoke. Код PDF/proposal/MockTransport, guards,
таблицы сценария и двухфазная fixture не менялись. Обратное преобразование импортов,
CLI-docstring и нормализация EOL восстановили исходные SHA256 всех четырёх файлов.

desktop_main больше не импортирует DesktopFixture при обычном старте. Только
явная args.smoke_test ветка импортирует scripts.smoke_fixture; PDF-фаза использует
тот же opt-in factory/guard. desktop_observations меняет только три QA-imports.
Все необходимые test/helper импорты обновлены. Команда из корня:

```powershell
.venv/Scripts/python.exe -B -m scripts.command_smoke --output reports/command-cycle.json
```

packaging/SoloRealmsAI.spec явно перечисляет три scripts QA-helper hiddenimports
для будущей явной frozen smoke-приёмки. Весь scripts/tests не собирается; pytest
по-прежнему исключён. Command smoke в desktop package не добавляется. Обычный
production-контур solo не содержит QA-transport, обычный desktop-import не загружает
QA scripts/PDF и не создаёт профиль. Положительное прежнее EXE-доказательство .5
остаётся историческим; новый EXE не собирался.

## Целевые проверки

```powershell
$env:SOLO_TEST='1'; $env:PYTHONDONTWRITEBYTECODE='1'
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_smoke_relocation.py tests/test_desktop_pdf_smoke.py --basetemp "$env:TEMP/solo-stage3-smoke-move-tests"
```

Первый прогон: **6 passed, 1 failed**. Прошли child-process import, отсутствие QA
при обычном desktop import, явные четыре helper imports без pytest/tests,
новый `python -m` --help, точечная package-схема и все CLI/profile guards.

Положительный registered PDF gate дошёл через настоящие upload/preparation/
confirm-world/Native creation/observe/conversation и остановился на check save-dex:
`Content changed after tree inventory` из параллельно добавленного
solo/content_verification_cache.py. Это не import/transport ошибка переноса.

Read-only воспроизведение на неизменённом dnd5e_engine/__init__.py:

- DirEntry.stat: size=4335, mtime_ns=ctime_ns=1792227377398119600, st_ino=0;
- fstat открытого файла: те же size/mtime/ctime, st_ino=18014398510068949.

Windows сравнивает две неодинаковые формы stat identity; текущая cache-проверка
считает это изменением дерева. Код cache не менялся и проверки не обходились.
Владельцу cache/root нужен совместимый identity seam, после него достаточно
повторить один упавший положительный PDF gate. Native/полный suite не запускался.
Это промежуточный результат; последующий узкий повтор описан ниже.

После подтверждённого freeze cache-owner
`reports/stage3-content-cache/freeze.json` повторён **только** упавший кейс:

```powershell
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_desktop_pdf_smoke.py::test_registered_pdf_hook_and_recreated_app_continue_without_repreparing --basetemp "$env:TEMP/solo-stage3-smoke-move-tests"
```

**1 passed, 13.81 с.** Прежние 6 PASS переиспользованы без перезапуска.
Существующий положительный hook проходит PDF → import → prepare → confirm →
Native Rogue → разговор/проверка → save → recreated app/continue с тем же
Native hash, без повторной подготовки. Новый путь scripts.smoke_fixture работает.
Ошибку Windows устранил её владелец; этот чат не редактировал cache. Всего
семь разных целевых проверок зелёные; это не full suite и не новый frozen EXE.

## Документация / список root

Обновлены собственные docs/GM_COMMAND_CYCLE.md и docs/PDF_FROZEN_SMOKE_FIXTURE.md.
Общий README.md:67 ещё содержит `python -m solo.command_smoke`; root должен заменить
только путь на scripts.command_smoke. Общие ROADMAP, PROTOTYPE_INTEGRATION и
исторические reports/source snapshots не переписывались.

Доказанные byte-identical assets/docs пары: COMBAT.md, INVENTORY.md, MAPS.md,
RULESETS.md, SRD_ATTRIBUTION.md, WORLD.md. DESKTOP.md отличается. Удаление не
выполнялось: assets являются packaged runtime-ресурсами, общие docs принадлежат
другим владельцам, а доказательства безопасной замены всех ссылок нет. Список
с SHA сохранён в freeze manifest для root. README-Prototype/README-Windows и
атрибуция не удалены ради сокращения числа файлов.

## Overlay / сохранность

reports/stage3-smoke-relocation-freeze.json перечисляет replacements с SHA,
removed_paths с исходными SHA256 до перемещения и read-only список duplicate docs.
Root может удалять в Solarimus AI только эти четыре прежних файла и только после
проверки старого SHA; при отличии файла остановиться, не затирать внешние правки.
Этот чат в Solarimus AI не писал. EXE/ZIP/dist/.3 snapshot и Drive не изменялись.

**Source freeze завершён.** После создания финального манифеста исходники этого
владельца больше не меняются. Замороженные replacement-файлы готовы для root
overlay; удаление прежних путей возможно только по recorded removed_sha256.

Очистка: собственные TestClient/дочерние процессы завершены. Один стенд
%TEMP%/solo-stage3-smoke-move-tests сохранён до приёмки overlay как положительный
пример PDF gate. Удалений/обхода прежнего policy-отказа не было.
