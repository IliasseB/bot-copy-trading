# 🚀 Guide de démarrage — Telegram Trading Bot

## ✅ Ce que tu as déjà (tout est configuré)
- Telegram API connecté à ton compte
- Canal de signaux configuré
- MT5 Pepperstone Demo configuré
- Risk management conservateur (1% par trade, stop 3% journalier)

---

## 📋 ÉTAPE 1 — Installe Python 3.12

1. Va sur **https://www.python.org/downloads/**
2. Télécharge **Python 3.12**
3. Lance l'installateur
4. ⚠️ **IMPORTANT** : Coche **"Add Python to PATH"** avant de cliquer Installer

---

## 📋 ÉTAPE 2 — Installe Docker Desktop

1. Va sur **https://www.docker.com/products/docker-desktop/**
2. Télécharge et installe Docker Desktop
3. Redémarre ton PC si demandé
4. Lance Docker Desktop et attends qu'il soit prêt (icône verte)

> Docker sert à lancer PostgreSQL et Redis automatiquement

---

## 📋 ÉTAPE 3 — Installe le bot

1. Dézippe le fichier `telegram_trading_bot.zip`
2. Ouvre le dossier `telegram_trading_bot`
3. Double-clique sur **`install_windows.bat`**
4. Attends que tout s'installe (2-5 minutes)

---

## 📋 ÉTAPE 4 — Lance MT5

1. Ouvre **Pepperstone MetaTrader 5**
2. Connecte-toi avec le compte démo (62123245)
3. Vérifie que **"Algo Trading"** est activé (bouton en haut dans MT5)
4. **Laisse MT5 ouvert** pendant que le bot tourne

---

## 📋 ÉTAPE 5 — Lance le bot

1. Double-clique sur **`START_BOT.bat`**
2. La **première fois**, Telegram va envoyer un code sur ton téléphone `+33XXXXXXXXX`
3. Entre le code dans la console
4. Le bot démarre et écoute le canal

---

## ✅ Le bot tourne quand tu vois :

```
✅ uvloop active
[OK] MT5 connecté — Balance: XXXX EUR
[OK] Telegram connecté — Canal: -2422625648
[BOT] En écoute...
```

---

## ⚠️ IMPORTANT

- **Ne ferme pas MT5** pendant que le bot tourne
- **Ne ferme pas la console** du bot
- La première semaine : surveille chaque trade exécuté
- Les logs sont dans le dossier `logs/`

---

## 🔴 Pour arrêter le bot

Appuie sur **CTRL+C** dans la console

---

## 📊 Vérifier les trades exécutés

Regarde dans MT5 → onglet **"Historique"** pour voir tous les ordres passés par le bot.
