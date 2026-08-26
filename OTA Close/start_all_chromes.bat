@echo off
chcp 65001 >nul
REM Launch every Chrome profile that has at least one OTA routed to it.
REM Profile / port / sites come from hub\data\routing.json.
set "HUB=%~dp0..\hub"
if not exist "%HUB%\launch_chrome.py" (
  echo [ERROR] hub not found: %HUB%
  pause
  exit /b 1
)
python "%HUB%\launch_chrome.py" all
pause
