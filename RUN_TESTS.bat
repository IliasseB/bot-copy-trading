@echo off
cd /d "%~dp0"
echo Lancement des tests (aucun ordre reel, aucune connexion Telegram)...
"%~dp0venv\Scripts\python.exe" -m pip install -r requirements-dev.txt --quiet
"%~dp0venv\Scripts\python.exe" -m pytest -q
pause
