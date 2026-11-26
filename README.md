# ⚡ Telegram Signal Copy-Trading Bot

Ultra-fast automated trading bot that listens to Telegram groups and executes trades on MetaTrader 5 in **< 300ms**.

---

## 🏗️ Architecture

```
Telegram Group Message
        │
        ▼ (Telethon — real-time)
 TelegramListener
        │
        ▼ asyncio.create_task()
  SignalParser
  ├── RegexParser    (~0.1ms)  ← primary
  └── NLPParser      (~10ms)   ← fallback if confidence < 0.6
        │
        ▼
  TradeExecutor
  ├── RiskManager.check()
  │   ├── Spread filter
  │   ├── Daily loss cap
  │   ├── Max simultaneous trades
  │   └── Lot sizing (fixed-fractional)
  └── MT5Bridge.send_order()
        │
        ▼
  Database (PostgreSQL + Redis)
```

---

## 📁 Project Structure

```
telegram_trading_bot/
├── main.py                   # Entry point — wires all subsystems
├── config.py                 # Pydantic settings (env-driven)
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── DEPLOYMENT.md             # Full VPS setup guide
│
├── telegram_listener/
│   └── listener.py           # Telethon real-time listener
│
├── parser/
│   ├── signal_model.py       # TradingSignal dataclass (Pydantic)
│   ├── regex_parser.py       # Primary fast parser (pre-compiled regex)
│   ├── nlp_parser.py         # NLP fallback (spaCy)
│   └── signal_parser.py      # Orchestrator + deduplication
│
├── execution/
│   ├── mt5_bridge.py         # Async MT5 API wrapper + retry
│   └── trade_executor.py     # Risk check → execute pipeline
│
├── risk_management/
│   └── risk_manager.py       # Lot sizing, daily loss, emergency stop
│
├── database/
│   └── db.py                 # asyncpg PostgreSQL + Redis
│
├── utils/
│   └── logger.py             # Structured JSON logging (structlog)
│
└── tests/
    ├── test_parser.py
    ├── test_risk_manager.py
    └── test_integration.py
```

---

## 🚦 Supported Signal Formats

```
BUY GOLD 3350 SL 3340 TP 3365
SELL XAUUSD NOW PE: 3348 SL: 3355 TP1: 3340 TP2: 3330
BUY XAUUSD
PENDING SELL GOLD @3360 SL 3370 TP 3340
🟢 BUY XAU/USD 📍Entry: 3350 🛑 SL: 3340 🎯 TP: 3365
buy gold sl 3340 tp 3365   ← lowercase OK
```

---

## ⚙️ Quick Start

```bash
# 1. Clone
git clone <repo>
cd telegram_trading_bot

# 2. Configure
cp .env.example .env
# Edit .env with your Telegram API keys, MT5 credentials, etc.

# 3. Start infrastructure
docker-compose up -d postgres redis

# 4. Install Python deps
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm

# 5. Run (first run will ask for Telegram verification code)
python main.py

# 6. Run tests
pytest tests/ -v
```

---

## 🛡️ Risk Management

| Parameter                  | Default | Description                         |
|----------------------------|---------|-------------------------------------|
| `RISK_MAX_PERCENT_PER_TRADE` | 1.0%  | Max account % risked per trade      |
| `RISK_MAX_DAILY_LOSS_PERCENT`| 5.0%  | Bot stops after this daily drawdown |
| `RISK_MAX_SIMULTANEOUS_TRADES`| 5    | Max open positions                  |
| `RISK_MAX_SPREAD_POINTS`   | 30      | Skip trade if spread too wide       |
| `RISK_MAX_SLIPPAGE_POINTS` | 20      | Log warning if slippage exceeded    |

---

## ⚡ Performance

| Stage                | Target latency  |
|----------------------|-----------------|
| Telegram receipt     | ~0ms (event)    |
| Regex parsing        | < 1ms           |
| NLP fallback         | < 50ms          |
| Risk check           | < 2ms           |
| MT5 execution        | 50–150ms        |
| **Total pipeline**   | **< 300ms** ✅  |

---

## ⚠️ Disclaimer

This software is for educational purposes. Trading carries significant financial risk.
Always test on a **demo account** before using with real funds.
The authors are not responsible for any trading losses.
