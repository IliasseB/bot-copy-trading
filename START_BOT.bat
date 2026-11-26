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
echo   TELEGRAM TRADING BOT - DEMARRAGE
echo ============================================================
echo.
:start
echo [BOT] Demarrage...
echo [BOT] Premiere fois = Telegram va demander un code de verification
echo [BOT] Entre le code que tu recois sur ton telephone
echo.
"%PY%" main.py
echo.
echo [BOT] Arrete. Appuie sur une touche pour relancer.
pause
goto start
