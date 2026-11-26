"""Horaires de trading et limite de signaux par jour."""
from datetime import datetime

from config import RiskConfig
from parser.signal_model import Direction, TradingSignal
from risk_management.risk_manager import RiskManager


def sig():
    return TradingSignal(telegram_msg_id=1, telegram_chat_id=1, raw_text="x", direction=Direction.BUY,
                         symbol="XAUUSD", entry=4066.0, stop_loss=4062.0, take_profits=[4072.0], confidence=0.9)


def rm(**kw):
    cfg = RiskConfig(fixed_lot=0.01, lot_per_tp=0.0, max_lot=0.05, max_spread_points=50, **kw)
    return RiskManager(cfg)


async def test_hors_horaires_refuse(monkeypatch):
    r = rm(trading_hours="08:00-22:00")
    monkeypatch.setattr(RiskManager, "_now", staticmethod(lambda: datetime(2026, 11, 25, 23, 30)))
    res = await r.check(sig(), 500.0, 10.0)
    assert not res.approved and "horaires" in res.reason


async def test_dans_les_horaires_accepte(monkeypatch):
    r = rm(trading_hours="08:00-22:00", trading_days="1-5")
    monkeypatch.setattr(RiskManager, "_now", staticmethod(lambda: datetime(2026, 11, 25, 15, 0)))
    assert (await r.check(sig(), 500.0, 10.0)).approved


async def test_limite_par_jour():
    r = rm(max_trades_per_day=2)
    for _ in range(2):
        assert (await r.check(sig(), 500.0, 10.0)).approved
        r.on_trade_opened()
    res = await r.check(sig(), 500.0, 10.0)
    assert not res.approved and "par jour" in res.reason


class StatsMT5:
    async def get_bot_risk_stats(self):
        return {"open_signals": 0, "pnl_today": 0.0, "signals_today": 3}


async def test_limite_par_jour_lue_sur_mt5_apres_redemarrage():
    r = RiskManager(RiskConfig(fixed_lot=0.01, lot_per_tp=0.0, max_lot=0.05, max_trades_per_day=3), StatsMT5())
    res = await r.check(sig(), 500.0, 10.0)
    assert not res.approved
