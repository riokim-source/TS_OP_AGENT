@echo off
chcp 65001 >nul
REM ============================================================
REM  Global Chrome (Viator + MyRealTrip)
REM
REM  Do NOT put chrome.exe flags here any more.
REM  Profile / port / sites come from hub\data\routing.json (single source of truth).
REM  Two launchers used to open the SAME port with DIFFERENT --user-data-dir,
REM  so whichever started first won and the other bot attached to a Chrome
REM  that was not logged in. That is what this indirection prevents.
REM ============================================================
set "HUB=%~dp0..\..\hub"
if not exist "%HUB%\launch_chrome.py" (
  echo [ERROR] hub not found: %HUB%
  echo         Open the hub console and launch Chrome from there instead.
  pause
  exit /b 1
)
python "%HUB%\launch_chrome.py" GLOBAL
if errorlevel 1 pause
