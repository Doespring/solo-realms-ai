@echo off
cd /d "%~dp0"
set SOLO_TEST=1
set PYTHONUTF8=1
".venv\Scripts\python.exe" desktop_main.py --experimental --offline-mock
if errorlevel 1 pause
