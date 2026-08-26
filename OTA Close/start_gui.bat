@echo off
chcp 65001 >nul
echo ========================================
echo OTA Close Bot - GUI
echo ========================================
echo.

cd /d "%~dp0"
echo Working dir: %CD%
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found in PATH.
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

echo Launching GUI ...
echo ========================================
python gui.py
set EC=%ERRORLEVEL%
echo ========================================
echo.
echo Exit code: %EC%
pause >nul
