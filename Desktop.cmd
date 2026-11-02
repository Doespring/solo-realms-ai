@echo off
cd /d "%~dp0"
set SOLO_TEST=1
set PYTHONUTF8=1
if exist "dist\SoloRealmsAI-Prototype\SoloRealmsAI.exe" (
  start "" "dist\SoloRealmsAI-Prototype\SoloRealmsAI.exe"
) else (
  ".venv\Scripts\python.exe" desktop_main.py --experimental
  if errorlevel 1 pause
)
