# Windows — preview.6

Запуск: `Desktop.cmd` либо `dist/SoloRealmsAI/SoloRealmsAI.exe`. Папка `_internal` должна находиться рядом с EXE. Установщик: `dist/SoloRealmsAI-Setup-2.1.4-preview.6.exe`.

Python не нужен готовой сборке. Требуются Windows 10 19041+/11, .NET и WebView2 Evergreen Runtime. Данные приложения отделены от программы: `%LOCALAPPDATA%/SoloRealmsAI`. Ключи защищены Windows CurrentUser DPAPI и не переносятся этим архивом.

Исходники: установить `requirements-desktop.txt` в `.venv`, выполнить `scripts/build_windows.ps1`. Компилятор Inno Setup находится в `build-tools/inno`. `scripts/verify_windows.ps1` проверяет установку в изолированном каталоге и отказывается менять чужую пользовательскую установку.

Текущая сборка до очистки прошла portable/installed smoke на этом компьютере. Чистая Windows VM не проверялась. По запросу пользователя удалены все старые кампании и тестовые профили. Повторная установка приложения сама по себе не удаляет новые пользовательские данные.
