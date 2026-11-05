@echo off
setlocal
cd /d "%~dp0"
title Solo Realms AI - Web
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing: .venv\Scripts\python.exe
  pause
  exit /b 1
)
rem Explicit project-local profile; existing AppData profiles are not moved.
rem SOLO_WEB_DATA_DIR can explicitly select another profile.
if defined SOLO_WEB_DATA_DIR (
  set "SOLO_DATA_DIR=%SOLO_WEB_DATA_DIR%"
) else (
  set "SOLO_DATA_DIR=%~dp0runtime\web-local"
)
echo Starting current source version at http://127.0.0.1:19215/
echo Data profile: "%SOLO_DATA_DIR%"
echo Existing AppData profiles remain unchanged.
echo Keep this window open. Press Ctrl+C to stop the server.
".venv\Scripts\python.exe" -X utf8 -B scripts\start_cloud.py --data-dir "%SOLO_DATA_DIR%" --port 19215 --open
set "SOLO_WEB_EXIT=%ERRORLEVEL%"
if not "%SOLO_WEB_EXIT%"=="0" (
  echo.
  echo Web server failed. Read the error above.
  pause
)
exit /b %SOLO_WEB_EXIT%
