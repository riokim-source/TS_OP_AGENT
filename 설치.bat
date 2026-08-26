@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"

rem Keep this file ASCII only.
rem cmd reads .bat in the ANSI codepage, so Korean text here breaks
rem line parsing. Korean messages live in hub/banner.py instead.
title TOURSTORY OP SYSTEM - Setup

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0hub\install.ps1"
python "hub\banner.py" install-end
pause
