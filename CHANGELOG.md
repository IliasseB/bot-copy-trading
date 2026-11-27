# Journal des changements

## Branche `ameliorations-kakri21`

### Sécurité
- Plus aucun mot de passe en clair dans le dépôt (identifiants MT5 retirés du prompt, docker-compose lit le `.env`).
- `api_hash` et mot de passe MT5 en `SecretStr` ; téléphone et secrets masqués dans tous les logs.
- `scripts/check_secrets.py` + hook pre-commit + vérification GitHub : un secret ne peut plus être publié.
- `SECURITY.md` : que faire après une fuite.
- Verrou d'instance unique : protège la session Telegram d'une double connexion.

### Lecture du canal
- Signaux incohérents (SL/TP du mauvais côté) refusés avec un message clair — 8 vraies fautes du canal évitées.
- Format d'entrée `@3360`.
- Le fil des réponses et le dernier signal survivent à un redémarrage.

### Trading
- Expiration des ordres en attente (`BOT_PENDING_EXPIRY_MINUTES`, désactivée par défaut).
- Horaires et jours de trading (`RISK_TRADING_HOURS`, `RISK_TRADING_DAYS`).
- Limite de signaux par jour (`RISK_MAX_TRADES_PER_DAY`, lue sur MT5 même après redémarrage).
- Plus de faux avertissement de slippage pour un ordre rempli dans la zone.
- Pause du polling si Telegram demande de ralentir (FloodWait).

### Suivi
- Notifications dans tes Messages enregistrés Telegram (`BOT_NOTIFY`) : trade ouvert, refusé, ordre du canal appliqué, erreurs.
- Récap du jour à 22 h (`BOT_DAILY_REPORT_HOUR`).
- Heartbeat + `STATUS_BOT.bat` : le bot tourne-t-il, que détient-il ?
- Vérification de cohérence du `.env` et résumé des réglages au démarrage.
- Rotation de `bot.log`, console Windows en UTF-8.

### Outils
- `tools.export_channel`, `tools.backtest`, `tools.add_image_template`, `tools.status`.
- `UPDATE_BOT.bat`, `RUN_TESTS.bat`, `INSTALL_HOOKS.bat`.

### Qualité
- Tests isolés du vrai `.env` (6 tests échouaient selon le PC) ; 2 tests cassés réparés ; ~200 tests.
- `requirements.txt` réduit aux dépendances utilisées (−torch, −transformers, −spacy) ; `requirements-dev.txt`.
- Lint ruff propre ; GitHub Actions : tests, lint, anti-secrets.
