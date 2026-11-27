# CONTEXTE COMPLET — TELEGRAM SIGNAL COPY-TRADING BOT

## CE QUE C'EST
Je développe un bot de copy-trading automatique en Python. Le bot écoute en temps réel un canal Telegram privé où un trader professionnel publie des signaux de trading sur l'or (XAUUSD), et exécute automatiquement les ordres sur MetaTrader 5 (Pepperstone démo).

**Objectif de latence** : Signal Telegram → Ordre exécuté en moins de 300ms.

---

## STACK TECHNIQUE
- Python 3.12 (Windows)
- Telethon (listener Telegram)
- MetaTrader 5 Python API
- asyncio (architecture entièrement async)
- PostgreSQL + Redis (optionnels, le bot tourne sans eux)
- Docker Desktop (pour PostgreSQL + Redis)
- Pydantic v2 (validation des données)
- structlog remplacé par stdlib logging (fix de compatibilité)

---

## CONFIGURATION (les secrets restent dans `.env`, jamais dans le dépôt)

### Telegram
- api_id / api_hash : dans le `.env` (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`), à créer sur https://my.telegram.org
- Numéro : dans le `.env` (`TELEGRAM_PHONE`)
- Canal surveillé : `-2422625648` (RentaTrade Z VIP — 5940 abonnés)
- Session Telethon : fichier `trading_bot.session` (créé au 1er lancement)

### MetaTrader 5 — Pepperstone Démo
- Login / mot de passe : dans le `.env` (`MT5_LOGIN`, `MT5_PASSWORD`) — jamais dans ce fichier
- Server : `PepperstoneUK-Demo`
- Path MT5 : `C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe`
- **Symbole de l'or sur Pepperstone MT5 : `XAUUSD`** (pas XAUUSDm)

### Risk Management
- Max % par trade : 1%
- Stop journalier : 3%
- Max trades simultanés : 3
- Max spread : 200 points (large pour éviter les blocages)
- Slippage max : 50 points

### Base de données
- PostgreSQL : `DATABASE_URL` dans le `.env`
- Redis : `redis://localhost:6379/0`
- ⚠️ Le bot fonctionne SANS PostgreSQL et Redis (mode dégradé, logs d'avertissement normaux)

---

## STRUCTURE DU PROJET
```
telegram_trading_bot/
├── main.py                         # Point d'entrée principal
├── config.py                       # Config Pydantic centralisée
├── .env                            # Variables d'environnement (configuré)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── install_windows.bat             # Installation (1 seule fois)
├── START_BOT.bat                   # Lancement quotidien
├── GUIDE_DEMARRAGE.md
│
├── telegram_listener/
│   └── listener.py                 # Telethon + catch-up toutes les 20s
│
├── parser/
│   ├── signal_model.py             # TradingSignal Pydantic (avec entry_low/entry_high)
│   ├── regex_parser.py             # Parser principal ultra-rapide
│   ├── nlp_parser.py               # Fallback spaCy si confiance < 0.6
│   └── signal_parser.py            # Orchestrateur + déduplication
│
├── execution/
│   ├── mt5_bridge.py               # Wrapper MT5 async + auto-détection symbole
│   └── trade_executor.py           # Pipeline: risk check → market/pending order
│
├── risk_management/
│   └── risk_manager.py             # Lot sizing, stop journalier, emergency stop
│
├── database/
│   └── db.py                       # asyncpg PostgreSQL
│
└── utils/
    └── logger.py                   # stdlib logging (structlog retiré)
```

---

## FORMAT EXACT DES SIGNAUX DU CANAL

Le canal s'appelle **RentaTrade Z VIP**. Le trader envoie UNIQUEMENT du XAUUSD/Gold.

### Format d'un signal (avec markdown Telegram brut) :
```
🚨 BUY XAUUSD **__

__****__➡️__****__ENTRY : 4055-4058

__****__🔴__****__SL : 4050

__****__🟢__****__TP1: 4062
__****__🟢__****__TP2: OPEN

__**⚠️**__Disclaimer : Il ne s'agit en aucun cas d'un conseil financier...
```

### Ce que le bot extrait :
- Direction : BUY ou SELL
- Symbole : XAUUSD (toujours l'or)
- Zone d'entrée : `4055-4058` → entry_low=4055, entry_high=4058
- SL : 4050
- TP1 : 4062
- TP2: OPEN → **ignoré** (pas de chiffre)
- Disclaimer → **ignoré**

### Logique de la zone d'entrée :
- **BUY** → prix cible = bas de la zone (4055) = meilleur prix d'achat
- **SELL** → prix cible = haut de la zone (4058) = meilleur prix de vente

### Logique Market vs Pending :
```
Si prix actuel EST dans la zone [4055-4058] (±2 points) → MARKET ORDER immédiat
Si prix actuel PAS dans la zone → PENDING ORDER au prix cible
```

### Messages à ignorer (le bot le fait déjà) :
- `GOLD BUY NOW` / `ATTENDEZ MON GO LA` → pré-alertes sans détails
- `✅ +30 PIPS DEPUIS 4033` → update de profit
- `✅ MODIFIER TP A 4027` → modification manuelle (sans signal complet)
- `✅ SÉCURISEZ 50% DU TRADE` → instruction manuelle
- `✅ CLÔTUREZ TP2 +80 PIPS` → clôture manuelle
- Messages vocaux, vidéos, livestreams
- Commentaires des membres du canal

### Messages MODIFIÉS (edits) :
L'admin corrige parfois ses signaux. Le bot écoute aussi les `MessageEdited`.
- Si pas encore exécuté → re-parse et exécute la version corrigée
- Si déjà exécuté → log d'alerte pour vérification manuelle sur MT5

---

## PROBLÈMES RENCONTRÉS ET SOLUTIONS APPLIQUÉES

### 1. Erreur `PrintLogger has no attribute 'name'`
**Cause** : structlog incompatible avec la version installée
**Fix** : Remplacé structlog par stdlib logging classique dans `utils/logger.py`

### 2. Canal Telegram non résolu (`Invalid object ID for a chat`)
**Cause** : Nouveau format d'ID Telegram Web (`-2422625648`) non reconnu directement par Telethon
**Fix** : 4 stratégies de résolution dans `_try_resolve()` — PeerChannel, format -100, iter_dialogs
**Résultat** : Le canal se résout maintenant via stratégie 2 (PeerChannel)

### 3. Signal détecté mais `Spread too wide: 999.0 > 30`
**Cause** : Symbole configuré en `XAUUSDm` → n'existe pas sur Pepperstone → spread illisible → 999
**Fix** : 
- Symbole changé en `XAUUSD` (confirmé par site officiel Pepperstone)
- Auto-détection du symbole au démarrage (essaie XAUUSD → XAUUSDm → GOLD)
- Fallback spread = 20 si illisible (au lieu de 999)
- Limite spread passée de 30 → 200

### 4. Signal non détecté à cause du markdown Telegram
**Cause** : Les messages contiennent `**__` `__****__` (gras/italique Telegram) qui cassaient les regex
**Fix** : `_RE_MARKDOWN = re.compile(r'(\*\*|__|~~|\|\||\*|`)`)` appliqué avant le parsing

### 5. `TP2: OPEN` crashait silencieusement le parser
**Cause** : Le trader écrit parfois TP2: OPEN (pas de valeur numérique)
**Fix** : Regex TP ne capture que les valeurs numériques > 100

### 6. Message perdu pendant coupure réseau (msg_id=8202)
**Cause** : Coupure WiFi/réseau de 1h15 → Telethon perd un message au rattrapage
**Fix** : Boucle catch-up toutes les 20s qui relit les 20 derniers messages du canal

### 7. Pas de logs quand le parser rejetait un signal
**Fix** : Logs détaillés ajoutés à chaque étape du parsing — on voit maintenant exactement pourquoi un signal est ignoré

---

## ÉTAT ACTUEL DU BOT (derniers logs confirmés)

```
✅ PostgreSQL connecté (si Docker lancé, sinon warning normal)
✅ Redis connecté (si Docker lancé, sinon warning normal)  
✅ MT5 connecté — login=62123245 server=PepperstoneUK-Demo balance=50000.0 EUR
✅ Groupe résolu — id=-2422625648 nom='RentaTrade Z VIP'
✅ Anti-doublon initialisé — 20 IDs en mémoire
✅ Bot en écoute — temps réel + catch-up toutes les 20s...
```

### Dernier test (01/07/2026) :
```
[8237] Parser → BUY XAUUSD zone=[4077-4079] sl=4073.0 tps=[4083.0] conf=1.00
✅ Signal accepté conf=1.00
⛔ Signal rejeté — Risk refusé: Spread too wide: 999.0 > 30
```
→ Ce problème est corrigé dans la dernière version (symbole XAUUSD + auto-détection)

---

## POUR LANCER LE BOT (chaque jour)

1. Ouvre **Docker Desktop** → attends l'icône verte (optionnel mais recommandé)
2. Ouvre **Pepperstone MT5** → connecte-toi → active **"Algo Trading"** (bouton en haut)
3. Double-clique **`START_BOT.bat`**
4. **Ne ferme pas** la console

`install_windows.bat` = **UNE SEULE FOIS** (déjà fait)

---

## CE QU'IL RESTE À FAIRE / AMÉLIORATIONS POSSIBLES

1. **Tester le premier trade réel en démo** → vérifier que l'ordre apparaît dans MT5
2. **Gestion multi-TP** → clôturer 50% à TP1, laisser courir à TP2
3. **Trailing stop** → déplacer le SL au prix d'entrée quand TP1 est atteint (breakeven)
4. **Dashboard web** → voir les trades en cours, P&L, logs en temps réel
5. **Notifications** → recevoir un SMS/Telegram quand un trade est ouvert/fermé
6. **Passer en compte live** → quand tout est validé en démo

---

## COMMANDE POUR RÉCUPÉRER LE ZIP DU BOT
Le fichier ZIP configuré est : `telegram_trading_bot_CONFIGURED.zip`
Il contient TOUS les fichiers avec TOUTES les configurations déjà remplies.

