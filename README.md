# Bot de copy-trading Telegram → MetaTrader 5

Le bot écoute un canal Telegram de signaux sur l'or (XAUUSD), ouvre les trades sur MetaTrader 5
en moins d'une seconde, puis **suit la gestion du canal** comme un membre attentif :
SL et TP modifiés, mise au break-even, clôtures manuelles, « TP1 HIT », « SL HIT »… y compris
quand le canal les poste **en image**.

## Ce que le bot sait faire

| Le canal publie… | Le bot… |
|---|---|
| `BUY XAUUSD / ENTRY 4066-4069 / SL 4062 / TP1 4072 / TP2 OPEN` | ouvre 1 position par TP (au marché si le prix est dans la zone, sinon ordre en attente au bord) |
| une correction du signal (message modifié) | met à jour SL/TP, ou replace les ordres en attente |
| « ❌ MODIFIER SL A 4060 », « ✅ MODIFIER TP1 A 4075 » | modifie le SL / le TP de la bonne position |
| « METTEZ BE SUR TP2 », image « METTEZ VOUS BE !! » | passe les positions au break-even |
| « TP1 touché », image « TP1 HIT » | encaisse TP1 s'il est encore ouvert et en profit, BE sur le reste |
| « CLÔTUREZ TP2 MANUELLEMENT », « PRENEZ VOTRE TP1 » | ferme uniquement cette position |
| « SÉCURISEZ 50 % » | clôture partielle |
| « BE HIT », « SL HIT », « OUT », « CLÔTUREZ TOUT » | ferme le reste et annule les ordres en attente |
| un signal avec une faute de frappe (SL du mauvais côté, entrée à 2 % du prix) | refuse et attend la correction |

Il ignore les conditions (« si on rejette… »), les conseils (« n'hésitez pas à… »), les infos (« +50 PIPS »)
et les textes pédagogiques. Les règles ont été calibrées sur ~4000 messages réels du canal.

## Installation (Windows)

1. Installer **Python 3.12** (cocher « Add Python to PATH ») et **MetaTrader 5** de ton courtier.
2. Double-cliquer sur `install_windows.bat` (crée le `venv` et installe les dépendances).
3. Copier `.env.example` en `.env` et le remplir : clés Telegram (https://my.telegram.org),
   identifiants MT5, ID du canal. **Ne jamais publier ce fichier.**
4. Double-cliquer sur `INSTALL_HOOKS.bat` (bloque tout commit contenant un secret).
5. Ouvrir MT5, se connecter, puis lancer `START_BOT.bat`.
   La première fois, Telegram envoie un code sur ton téléphone : tape-le dans la fenêtre.

| Script | Rôle |
|---|---|
| `START_BOT.bat` | lance le bot (refuse s'il tourne déjà) |
| `STATUS_BOT.bat` | le bot tourne-t-il ? quelles positions a-t-il ? (lecture seule) |
| `UPDATE_BOT.bat` | récupère la dernière version et met à jour les packages |
| `RUN_TESTS.bat` | lance les tests (aucun ordre réel, aucune connexion) |
| `TEST_MT5.bat` | vérifie la connexion MT5 sans Telegram |

## Réglages principaux (`.env`)

| Variable | Défaut | Effet |
|---|---|---|
| `RISK_LOT_PER_TP` | 0 | lot par TP : 1 TP → 1 position, TP1 + TP2 → 2 positions |
| `RISK_FIXED_LOT` | 0 | lot fixe si `RISK_LOT_PER_TP=0` (0 = calcul selon le risque) |
| `RISK_MAX_LOT` | 1.0 | plafond dur du lot total d'un signal |
| `RISK_MAX_SIMULTANEOUS_TRADES` | 5 | signaux ouverts en même temps |
| `RISK_MAX_DAILY_LOSS_PERCENT` | 0 | arrêt du jour après X % de perte (0 = jamais) |
| `RISK_MAX_TRADES_PER_DAY` | 0 | signaux tradés par jour (0 = illimité) |
| `RISK_TRADING_HOURS` / `RISK_TRADING_DAYS` | vide | ex. `08:00-22:00` et `1-5` |
| `RISK_MAX_ENTRY_DISTANCE_PERCENT` | 1.0 | entrée trop loin du prix = faute de frappe → refusé |
| `EXECUTION_BE_OFFSET_POINTS` | 0 | BE à entrée + X points (couvre la commission) |
| `BOT_PENDING_EXPIRY_MINUTES` | 0 | annule un ordre en attente après X min (0 = jamais) |
| `BOT_UPDATE_PARTIAL_CLOSE` | true | applique « SÉCURISEZ 50 % » |
| `BOT_UPDATE_NO_REPLY_FALLBACK` | true | un ordre clair posté sans « répondre » vise le dernier signal |
| `BOT_NOTIFY` | false | le bot t'écrit dans tes Messages enregistrés Telegram |
| `BOT_DAILY_REPORT_HOUR` | 22 | heure du récap du jour (-1 = jamais) |
| `MT5_ALLOW_REAL` | false | sécurité : `true` obligatoire pour trader sur un compte réel |

Au démarrage, le bot affiche un résumé de ces réglages et signale les combinaisons incohérentes
(par ex. `RISK_MAX_LOT` trop petit pour ouvrir TP2).
