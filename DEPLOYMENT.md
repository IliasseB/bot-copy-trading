# 🚀 VPS Deployment Guide — Telegram Signal Copy-Trading Bot

## ── RECOMMENDED VPS SPECS ─────────────────────────────────────────────────

| Component     | Minimum          | Recommended            |
|---------------|------------------|------------------------|
| OS            | Windows Server 2019 | Windows Server 2022  |
| CPU           | 2 vCPU           | 4 vCPU                 |
| RAM           | 4 GB             | 8 GB                   |
| Disk          | 40 GB SSD        | 80 GB NVMe SSD         |
| Network       | 100 Mbps         | 1 Gbps                 |
| Location      | Same country as broker server | Same datacenter if possible |

> ⚠️ MetaTrader 5 requires **Windows**. Use a Windows VPS.
> The Python bot + Docker can run on Linux but MT5 terminal must be Windows.

---

## ── STEP 1: VPS PROVIDER ──────────────────────────────────────────────────

**Recommended providers (low-latency)**:
- Contabo (cheap, EU/US)
- Vultr (NYC/London/Frankfurt)
- DigitalOcean (Droplets)
- Kamatera (bare-metal speed)
- ForexVPS.net (broker-optimised)

---

## ── STEP 2: WINDOWS SETUP ────────────────────────────────────────────────

```powershell
# 1. Install Chocolatey (Windows package manager)
Set-ExecutionPolicy Bypass -Scope Process -Force
iex ((New-Object System.Net.WebClient).DownloadString('https://chocolatey.org/install.ps1'))

# 2. Install Python 3.12
choco install python312 -y

# 3. Install Git
choco install git -y

# 4. Install Docker Desktop (optional — for PostgreSQL + Redis)
choco install docker-desktop -y

# 5. Install MetaTrader 5
# Download from your broker's website and install normally
```

---

## ── STEP 3: INSTALL METATRADER 5 ────────────────────────────────────────

1. Download MT5 from your broker
2. Install and login with your trading account
3. Enable **AutoTrading** (F7 → Expert Advisors → Allow algorithmic trading)
4. Enable **DLL imports**: Tools → Options → Expert Advisors → Allow DLL imports
5. Note the path to `terminal64.exe` (usually `C:\Program Files\MetaTrader 5\terminal64.exe`)

---

## ── STEP 4: CLONE AND CONFIGURE ─────────────────────────────────────────

```bash
git clone https://github.com/youruser/telegram-trading-bot.git
cd telegram-trading-bot

# Copy and edit the env file
copy .env.example .env
notepad .env   # Fill in all values
```

**Critical `.env` values to fill in:**
```env
TELEGRAM_API_ID=           # from https://my.telegram.org/apps
TELEGRAM_API_HASH=         # from https://my.telegram.org/apps
TELEGRAM_PHONE=+1234567890
TELEGRAM_GROUP_IDS=-1001234567890

MT5_LOGIN=12345678
MT5_PASSWORD=YourPassword
MT5_SERVER=YourBroker-Live
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```

---

## ── STEP 5: INSTALL DEPENDENCIES ────────────────────────────────────────

```bash
# Create virtual environment
python -m venv venv
venv\Scripts\activate       # Windows
# or
source venv/bin/activate    # Linux

# Install packages
pip install --upgrade pip
pip install -r requirements.txt

# MetaTrader5 (Windows only)
pip install MetaTrader5

# spaCy model (NLP fallback)
python -m spacy download en_core_web_sm
```

---

## ── STEP 6: START POSTGRESQL + REDIS WITH DOCKER ────────────────────────

```bash
# Start only the infrastructure (not the bot)
docker-compose up -d postgres redis

# Verify they're running
docker-compose ps

# Check PostgreSQL
docker exec -it trading_postgres psql -U trading_user -d trading_bot -c "\dt"
```

---

## ── STEP 7: FIRST RUN (Interactive — to authenticate Telegram) ──────────

```bash
# Run once interactively to enter your Telegram verification code
python main.py

# You'll see: "Please enter the code you received:"
# Enter the code sent to your Telegram phone
# After login, a session file is saved — future runs are automatic
```

---

## ── STEP 8: RUN AS WINDOWS SERVICE (Auto-restart) ───────────────────────

### Option A: NSSM (recommended)
```powershell
# Download NSSM
choco install nssm -y

# Create service
nssm install TradingBot "C:\trading-bot\venv\Scripts\python.exe" "C:\trading-bot\main.py"
nssm set TradingBot AppDirectory "C:\trading-bot"
nssm set TradingBot AppStdout "C:\trading-bot\logs\service.log"
nssm set TradingBot AppStderr "C:\trading-bot\logs\service_err.log"
nssm set TradingBot AppRestartDelay 5000
nssm set TradingBot Start SERVICE_AUTO_START

# Start service
nssm start TradingBot

# Check status
nssm status TradingBot
```

### Option B: Task Scheduler (simpler)
```powershell
$action = New-ScheduledTaskAction -Execute "C:\trading-bot\venv\Scripts\python.exe" -Argument "C:\trading-bot\main.py" -WorkingDirectory "C:\trading-bot"
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "TradingBot" -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
Start-ScheduledTask -TaskName "TradingBot"
```

---

## ── STEP 9: LINUX DEPLOYMENT (WITHOUT MT5 — Mock mode) ─────────────────

```bash
# On Ubuntu 22.04
sudo apt update && sudo apt install -y python3.12 python3.12-venv docker.io docker-compose

# Clone and setup
git clone ...
cd telegram-trading-bot
cp .env.example .env && nano .env

# Start full stack
docker-compose up -d

# View logs
docker-compose logs -f bot
```

---

## ── MONITORING ───────────────────────────────────────────────────────────

```bash
# Live logs
docker-compose logs -f bot

# Daily stats
docker exec -it trading_postgres psql -U trading_user -d trading_bot \
  -c "SELECT status, count(*), avg(exec_ms) FROM signals WHERE created_at >= CURRENT_DATE GROUP BY status;"

# Redis dedup cache
docker exec -it trading_redis redis-cli --pass yourpassword KEYS "dedup:*" | wc -l
```

---

## ── LATENCY TUNING ───────────────────────────────────────────────────────

| Optimisation                        | Expected gain  |
|-------------------------------------|----------------|
| uvloop enabled                      | ~30% faster IO |
| VPS same city as broker server      | -20 to -50ms   |
| Persistent MT5 connection           | -100ms startup |
| Redis for dedup (vs disk)           | -5ms           |
| asyncio.create_task (non-blocking)  | pipeline parallel |
| Pre-compiled regex patterns         | -0.5ms parse   |

---

## ── SECURITY CHECKLIST ───────────────────────────────────────────────────

- [ ] `.env` file is NOT committed to git (check `.gitignore`)
- [ ] MT5 terminal uses a **demo account** for initial testing
- [ ] `RISK_MAX_DAILY_LOSS_PERCENT` set conservatively (start with 2%)
- [ ] `RISK_MAX_SIMULTANEOUS_TRADES` set to 3 initially
- [ ] VPS firewall: only allow ports 5432 and 6379 from localhost
- [ ] Telegram session file backed up securely
- [ ] Windows Defender configured to exclude MT5 folder

---

## ── UPDATING THE BOT ─────────────────────────────────────────────────────

```bash
cd telegram-trading-bot
git pull
pip install -r requirements.txt  # update deps if needed

# Restart service
nssm restart TradingBot
# or
docker-compose restart bot
```
