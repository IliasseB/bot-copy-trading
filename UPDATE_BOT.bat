@echo off
cd /d "%~dp0"
echo ============================================================
echo   MISE A JOUR DU BOT
echo ============================================================
echo [1/2] Recuperation de la derniere version...
git pull --ff-only
IF %ERRORLEVEL% NEQ 0 (
    echo [ERREUR] Mise a jour impossible : tu as des modifications locales ou pas de connexion.
    pause
    exit /b 1
)
echo [2/2] Mise a jour des packages...
"%~dp0venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
echo [OK] Bot a jour. Relance START_BOT.bat
pause
