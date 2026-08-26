@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

rem Keep this file ASCII only.
rem cmd reads .bat in the ANSI codepage, so Korean text here breaks
rem line parsing. Korean messages live in hub/banner.py instead.
rem If it is already running, just open the browser.
netstat -ano | findstr /R /C:"127.0.0.1:8610 .*LISTENING" > nul 2>&1
if not errorlevel 1 goto ALREADY
netstat -ano | findstr /R /C:"0.0.0.0:8610 .*LISTENING" > nul 2>&1
if not errorlevel 1 goto ALREADY

title TOURSTORY OP SYSTEM

rem Local only (127.0.0.1), so no password is asked.
set LMHUB_LOCAL_ONLY=1

python "hub\banner.py" hub
python -m streamlit run "hub\op_ui\app.py" --server.address 127.0.0.1 --server.port 8610 --browser.gatherUsageStats false

python "hub\banner.py" hub-end
pause
exit /b 0

:ALREADY
python "hub\banner.py" hub-already
start http://localhost:8610
timeout /t 3 > nul
exit /b 0
