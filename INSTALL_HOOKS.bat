@echo off
cd /d "%~dp0"
git config core.hooksPath .githooks
echo [OK] Protection anti-secrets activee : chaque commit est verifie.
pause
