# Contribuer

1. Créer une branche : `git checkout -b ma-modif`.
2. Activer la protection anti-secrets une fois : `INSTALL_HOOKS.bat`.
3. Faire des commits **petits et ciblés**, messages en français :
   `feat(...)`, `fix(...)`, `docs(...)`, `test(...)`, `refactor(...)`, `sec(...)`, `chore(...)`.
4. Avant de pousser : `RUN_TESTS.bat` et `python -m ruff check .` doivent être verts.
5. Ouvrir une pull request : GitHub relance tests, lint et détection de secrets.

## Règles
- **Jamais** de `.env`, de `.session` ou de mot de passe dans le dépôt (voir [SECURITY.md](SECURITY.md)).
- Toute nouvelle règle de lecture du canal s'accompagne d'un test avec le **message réel**
  (`tests/test_rtz_channel.py`) et, si possible, d'un contrôle sur l'historique exporté.
- Un changement qui modifie la façon de trader (lot, entrée, sorties) est **désactivé par défaut**
  et documenté dans `.env.example`.
