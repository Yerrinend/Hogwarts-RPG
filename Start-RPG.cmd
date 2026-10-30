@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -File "%~dp0scripts\sync-windows.ps1" -Action pull
if errorlevel 1 (
  echo Synchronization stopped. Do not start a new RPG turn until resolved.
  pause
  exit /b 1
)
echo RPG files are current. Open this folder in Codex, then send your next choice.
pause
