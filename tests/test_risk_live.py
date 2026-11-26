"""Limites de risque lues en direct sur MT5 (trades simultanés + perte max du jour)."""
import pytest
from config import RiskConfig
from parser.signal_model import TradingSignal
from risk_management.risk_manager import RiskManager


class Bridge:
    def __init__(self, open_signals=0, pnl_today=0.0):
        self.stats = {"open_signals": open_signals, "pnl_today": pnl_today}
    async def get_bot_risk_stats(self): return self.stats
    async def get_symbol_trade_info(self, s):
        return {"tick_value": 1.0, "tick_size": 0.01, "volume_min": 0.01, "volume_max": 50, "volume_step": 0.01}
    async def get_margin_info(self, *a): return None


def sig():
    return TradingSignal(telegram_msg_id=1, telegram_chat_id=1, raw_text="x", direction="BUY",
                         symbol="XAUUSD", entry_low=4066, entry_high=4069, stop_loss=4064, take_profits=[4072], confidence=0.9)


def rm(bridge):
    return RiskManager(RiskConfig(fixed_lot=0.01, max_lot=0.01, max_simultaneous_trades=2,
                                  max_daily_loss_percent=3.0), bridge)


@pytest.mark.asyncio
async def test_trades_fermes_ne_bloquent_plus():
    b = Bridge(open_signals=0); r = rm(b)
    for _ in range(5):                       # avant le fix : bloqué dès le 3e signal
        r.on_trade_opened()
    assert (await r.check(sig(), 500, 15)).approved


@pytest.mark.asyncio
async def test_limite_trades_simultanes():
    assert not (await rm(Bridge(open_signals=2)).check(sig(), 500, 15)).approved


@pytest.mark.asyncio
async def test_perte_max_du_jour_declenchee():
    # 500 € en début de journée, -16 € réalisés → 3.2 % > 3 % → STOP
    assert not (await rm(Bridge(pnl_today=-16.0)).check(sig(), 484, 15)).approved


@pytest.mark.asyncio
async def test_petite_perte_ok():
    assert (await rm(Bridge(pnl_today=-10.0)).check(sig(), 490, 15)).approved


@pytest.mark.asyncio
async def test_perte_max_desactivee_a_zero():
    r = RiskManager(RiskConfig(fixed_lot=0.01, max_lot=0.01, max_daily_loss_percent=0), Bridge(pnl_today=-400))
    assert (await r.check(sig(), 100, 15)).approved


@pytest.mark.asyncio
async def test_lot_par_tp():
    s = sig(); s.take_profits = [4072, 4076]
    r = RiskManager(RiskConfig(lot_per_tp=0.01, max_lot=0.05), Bridge())
    assert (await r.check(s, 500, 15)).lot_size == 0.02
