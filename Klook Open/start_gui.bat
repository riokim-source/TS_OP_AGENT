@echo off
REM Klook Inventory Open - Desktop GUI
cd /d "%~dp0"
python gui.py
if errorlevel 1 (
    echo.
    echo [ERROR] GUI failed to start. See the message above.
    pause
)
