# Этап 2 — стык Interpreter capabilities

18 октября 2026. Только завершающий GM-стык; механические доказательства владельца
Правил из docs/NAT20_STAGE2.md переиспользованы, повторный engine-прогон не делался.

Изменено:

- solo/game.py: SceneCompiler.command_context включает готовый backend
  capabilities.spell_execution в существующий whitelist. Возвращается исходный
  компактный словарь только выбранных заклинаний. Полный каталог/счётчики
  заклинаний/механика в компилятор не добавлены.
- prompts/gm_commands_v1.txt: при активном бою narrative_or_unsupported_in_combat
  не предлагается как cast_spell; blocked и локальные дефекты Bless/Hide из
  known_issues требуют честного unsupported. Остальная магия/класс остаются
  доступны через прежние схемы. Native candidate не обещает успеха; вне боя
  отсутствие механики не разрешает самостоятельно применить эффект.
- tests/test_stage2_interpreter_capabilities.py: два узких теста compiled context
  и фактического Interpreter HTTP-пакета через CloudLLM + MockTransport.

Результат: **2 passed**, 6.07 с. Проверены реальный Native wizard в бою, выбранные
статусы unsupported/Bless/Magic Missile, known_issues Hide/Bless, отсутствие обхода
каталога при компиляции, SECRET_CANARY в мировых фактах/secret/скрытом NPC не виден
ни в контексте, ни в wire. Модельный ответ с Magic Missile проходит прежнюю схему,
три локально ограниченных ответа честно возвращают unsupported; Interpreter не
меняет кампанию. Ответы scripted: это проверка контракта и передачи инструкций,
а не доказательство следования живой моделью промпту.

```powershell
$env:SOLO_TEST='1'; $env:PYTHONDONTWRITEBYTECODE='1'
.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider tests/test_stage2_interpreter_capabilities.py --basetemp "$env:TEMP/solo-stage2-interpreter-tests"
```

Cloud этапа 1, Native worker/DTO/guards, память, command-engine routing и death
capabilities не менялись. Этап 3 не выполнялся. Ноль live/paid; full/EXE/ZIP/Drive
не запускались. В C:/Users/iwa41/Documents/Solarimus AI этот чат не писал.

Source freeze: reports/stage2-interpreter-freeze.json содержит точные SHA256 файлов
для root overlay. Новый report не заменяет механический отчёт владельца Правил.

Очистка: собственных активных тестов/серверов нет; единственный новый стенд
%TEMP%/solo-stage2-interpreter-tests сохранён до приёмки overlay. Прежний policy
отказ удаления не обходился. Архивы и чужие пользовательские базы не тронуты.
