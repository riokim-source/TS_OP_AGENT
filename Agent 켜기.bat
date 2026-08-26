@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

rem Keep this file ASCII only.
rem cmd reads .bat in the ANSI codepage, so Korean text here breaks
rem line parsing. Korean messages live in hub/banner.py instead.
title TOURSTORY OP - Agent

rem Runs jobs sent from the central screen on this PC's Chrome.
rem Login never leaves this PC.
set LMHUB_QUEUE=firebase

python "hub\banner.py" agent
python "hub\agent.py"
python "hub\banner.py" agent-end
pause
