@echo off
cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERREUR] venv introuvable : %PY%
    echo Lance install_windows.bat d'abord.
    pause
    exit /b 1
)
echo ============================================================
echo   TEST DU BOT - VERIFICATION MT5 (SANS TELEGRAM)
echo ============================================================
echo.
"%PY%" test_mt5_connexion.py
pause
