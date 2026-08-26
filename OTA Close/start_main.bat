@echo off
chcp 65001 >nul
echo ========================================
echo OTA Close Bot - CLI
echo ========================================
echo.

cd /d "%~dp0"
echo Working dir: %CD%
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found in PATH.
    echo Install Python from https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version
echo.

if exist ".venv\Scripts\activate.bat" (
    echo .venv found - activating
    call ".venv\Scripts\activate.bat"
)
echo.

echo Running main.py ...
echo ========================================
python main.py
set EC=%ERRORLEVEL%
echo ========================================
echo.
echo Exit code: %EC%
echo Press any key to close.
pause >nul
