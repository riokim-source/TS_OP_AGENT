@echo off
REM Klook Inventory Open - Mobile remote-control server (default port 8500)
REM Keep this window open while using the phone.
cd /d "%~dp0"
set "PYTHONUTF8=1"
python mobile_server.py %1
pause
