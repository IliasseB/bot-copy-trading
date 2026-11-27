# Sécurité

Ce bot manipule des accès sensibles : ton compte Telegram, ton compte MetaTrader 5 et de l'argent.

## Ce qui ne doit JAMAIS être dans le dépôt
| Fichier / valeur | Pourquoi |
|---|---|
| `.env` | api_id / api_hash Telegram, numéro, identifiants MT5, mots de passe |
| `*.session`, `*.session-journal` | session Telegram : permet d'utiliser ton compte **sans code** |
| `logs/` | contient le texte des signaux, tickets, soldes |

Ils sont dans `.gitignore`, et `scripts/check_secrets.py` bloque le commit s'ils y arrivent quand même.

## Activer la protection anti-secrets
Double-clique sur `INSTALL_HOOKS.bat` une fois. Ensuite, chaque `git commit` est vérifié :
fichier interdit, valeur du `.env`, jeton GitHub (`ghp_…`), api_hash ou mot de passe écrit en clair → commit refusé.

Vérifier tout le dépôt à la main :
```
python scripts/check_secrets.py --all
```

## Si un secret a été publié
Supprimer le fichier dans un nouveau commit **ne suffit pas** : il reste dans l'historique Git.
Il faut **changer le secret** :

1. **Telegram (api_id / api_hash)** : https://my.telegram.org → *API development tools*.
   Puis Telegram → Paramètres → Appareils → déconnecter les sessions inconnues.
2. **Session Telegram (`.session`)** : Telegram → Paramètres → Appareils → *Terminer toutes les autres sessions*,
   puis supprimer `trading_bot.session` et relancer le bot (nouveau code SMS).
3. **MetaTrader 5** : changer le mot de passe dans MT5 (Outils → Options → Serveur) ou chez le courtier.
4. **Jeton GitHub (`ghp_…`)** : GitHub → Settings → Developer settings → Personal access tokens → *Delete*.
   Ne jamais mettre de jeton dans l'URL d'un remote (`https://user:ghp_…@github.com/...`).
5. **Mots de passe PostgreSQL / Redis** : les changer dans `.env` puis `docker compose up -d --force-recreate`.

## Compte réel
Le bot refuse de trader sur un compte réel tant que `MT5_ALLOW_REAL=false` dans `.env`.
