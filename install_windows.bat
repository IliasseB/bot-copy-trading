@echo off
echo ============================================================
echo   TELEGRAM TRADING BOT - INSTALLATION WINDOWS
echo ============================================================
echo.

:: 1. Verifier Python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [ERREUR] Python n'est pas installe !
    echo Telecharge Python 3.12 sur https://python.org/downloads
    echo Coche bien "Add Python to PATH" pendant l'installation
    pause
    exit /b 1
)
echo [OK] Python detecte

:: 2. Creer environnement virtuel
echo.
echo [1/5] Creation de l'environnement virtuel...
python -m venv venv
echo [OK] venv cree

:: 3. Activer venv
echo.
echo [2/5] Activation du venv...
call venv\Scripts\activate.bat

:: 4. Mettre a jour pip
echo.
echo [3/5] Mise a jour de pip...
python -m pip install --upgrade pip --quiet

:: 5. Installer les dependances
echo.
echo [4/5] Installation des packages Python...
pip install telethon==1.36.0 --quiet
pip install asyncpg==0.29.0 --quiet
pip install "redis[hiredis]==5.0.3" --quiet
pip install SQLAlchemy==2.0.28 --quiet
pip install python-dotenv==1.0.1 --quiet
pip install "pydantic==2.6.3" --quiet
pip install "pydantic-settings==2.2.1" --quiet
pip install structlog==24.1.0 --quiet
pip install orjson==3.9.15 --quiet
pip install tenacity==8.2.3 --quiet
pip install Pillow --quiet
pip install MetaTrader5 --quiet
pip install uvloop --quiet 2>nul || echo [INFO] uvloop non dispo sur Windows, on continue sans
echo [OK] Packages installes

:: 6. Telecharger Docker Desktop si pas present
echo.
echo [5/5] Verification Docker...
docker --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [INFO] Docker non detecte.
    echo Telecharge Docker Desktop sur : https://www.docker.com/products/docker-desktop/
    echo Installe-le puis relance ce script.
) ELSE (
    echo [OK] Docker detecte
    echo Demarrage PostgreSQL et Redis...
    docker-compose up -d postgres redis
)

echo.
echo ============================================================
echo   INSTALLATION TERMINEE !
echo ============================================================
echo.
echo Prochaine etape : lance START_BOT.bat
echo.
pause
