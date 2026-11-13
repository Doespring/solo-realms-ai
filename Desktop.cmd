@echo off
cd /d "%~dp0"
if exist "dist\SoloRealmsAI\SoloRealmsAI.exe" (
  start "" "dist\SoloRealmsAI\SoloRealmsAI.exe"
) else (
  echo Build the Windows app first: powershell -File scripts\build_windows.ps1
  pause
)
