@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" scripts\start_cloud.py --open
if errorlevel 1 pause
