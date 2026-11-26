"""
Isolation des tests : ils ne doivent JAMAIS lire le vrai .env (lot, risque, clés...)
ni des variables d'environnement du PC. Sinon un test passe chez l'un et échoue chez l'autre
(c'était le cas : RISK_FIXED_LOT=0.01 du .env faisait échouer 6 tests).
"""
import os

import pytest

PREFIXES = ("TELEGRAM_", "MT5_", "RISK_", "BOT_", "EXECUTION_", "DATABASE_URL", "REDIS_URL")


@pytest.fixture(autouse=True)
def _sans_env_reel(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith(PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)          # .env relatif au dossier courant → introuvable
    yield
