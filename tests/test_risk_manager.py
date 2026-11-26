"""
tests/test_risk_manager.py
Inclut les tests du bug « lot = 10.0 / No money » (retcode 10019).
"""
from datetime import date

import pytest

from config import RiskConfig
from parser.signal_model import Direction, TradingSignal
from risk_management.risk_manager import RiskManager, floor_to_step


class FakeBridge:
    """Simule MT5 / Pepperstone XAUUSD : 1 tick = 0.01, ~0.85 € par tick et par lot."""

    def __init__(self, free_margin=50000.0, margin_per_lot=2000.0, vol_max=100.0):
        self.free_margin = free_margin
        self.margin_per_lot = margin_per_lot
        self.vol_max = vol_max

    async def get_symbol_trade_info(self, symbol):
        return {"tick_value": 0.85, "tick_size": 0.01,
                "volume_min": 0.01, "volume_max": self.vol_max, "volume_step": 0.01}

    async def get_margin_info(self, symbol, direction, lot, price):
        return {"margin_required": lot * self.margin_per_lot, "free_margin": self.free_margin}


def make_cfg(**kw):
    base = dict(max_percent_per_trade=1.0, max_daily_loss_percent=5.0,
                max_simultaneous_trades=3, max_spread_points=30,
                max_slippage_points=20, default_lot_size=0.01)
    base.update(kw)
    return RiskConfig(**base)


def make_signal(**kwargs):
    defaults = dict(
        telegram_msg_id=1, telegram_chat_id=1,
        raw_text="BUY GOLD 3350 SL 3340 TP 3365",
        direction=Direction.BUY, symbol="XAUUSD",
        entry=3350.0, stop_loss=3340.0, take_profits=[3365.0], confidence=0.9,
    )
    defaults.update(kwargs)
    # TP cohérent avec l'entrée demandée (un BUY à 4124 ne peut pas viser 3365)
    if "take_profits" not in kwargs and defaults["entry"]:
        defaults["take_profits"] = [defaults["entry"] + 15.0]
    return TradingSignal(**defaults)


@pytest.fixture
def risk():
    return RiskManager(make_cfg())


class TestFloorToStep:
    def test_floors_down(self):
        assert floor_to_step(0.0299, 0.01) == 0.02
        assert floor_to_step(0.019, 0.01) == 0.01

    def test_exact_values_unchanged(self):
        assert floor_to_step(0.3, 0.01) == 0.3
        assert floor_to_step(0.07, 0.01) == 0.07


class TestChecks:
    async def test_approved_normal_signal(self, risk):
        result = await risk.check(make_signal(), 10000.0, 5.0)
        assert result.approved is True and result.lot_size > 0

    async def test_spread_too_wide(self, risk):
        result = await risk.check(make_signal(), 10000.0, 50.0)
        assert result.approved is False and "Spread" in result.reason

    async def test_max_trades_reached(self, risk):
        risk._open_trades = 3
        result = await risk.check(make_signal(), 10000.0, 5.0)
        assert result.approved is False and "simultaneous" in result.reason.lower()

    async def test_emergency_stop(self, risk):
        risk.trigger_emergency_stop("test")
        result = await risk.check(make_signal(), 10000.0, 5.0)
        assert result.approved is False and "EMERGENCY" in result.reason

    async def test_daily_loss_limit(self, risk):
        risk._daily.date_str = date.today().isoformat()  # sinon les stats sont remises à zéro
        risk._daily.total_loss = 600.0                    # 6 % de 10 000 > limite 5 %
        result = await risk.check(make_signal(), 10000.0, 5.0)
        assert result.approved is False

    def test_slippage_within_tolerance(self, risk):
        assert risk.check_slippage(3350.0, 3350.05, "XAUUSD") is True

    def test_slippage_exceeded(self, risk):
        assert risk.check_slippage(3350.0, 3352.5, "XAUUSD") is False


class TestLotSizing:
    async def test_bug_lot_10_not_reproduced(self):
        """Cas réel du test MT5 : balance 50 000 €, SL à 7 points. Ne doit plus donner 10.0."""
        risk = RiskManager(make_cfg(), FakeBridge())
        sig = make_signal(entry=4124.0, stop_loss=4117.0)
        lot = await risk._compute_lot(sig, 50000.0)
        # perte/lot = 700 ticks * 0.85 = 595 ; risque voulu = 500 -> 0.84 lot
        assert lot == 0.84

    async def test_lot_never_rounded_up(self):
        risk = RiskManager(make_cfg(), FakeBridge())
        sig = make_signal(entry=4124.0, stop_loss=4117.0)
        lot = await risk._compute_lot(sig, 50000.0)
        assert lot * 595.0 <= 500.0 + 1e-6   # risque réel <= risque voulu

    async def test_hard_cap_max_lot(self):
        risk = RiskManager(make_cfg(max_lot=1.0), FakeBridge())
        lot = await risk._compute_lot(make_signal(entry=4124.0, stop_loss=4117.0), 5_000_000.0)
        assert lot == 1.0

    async def test_fixed_lot_mode(self):
        risk = RiskManager(make_cfg(fixed_lot=0.01), FakeBridge())
        lot = await risk._compute_lot(make_signal(entry=4124.0, stop_loss=4117.0), 50000.0)
        assert lot == 0.01

    async def test_small_account_uses_min_lot(self):
        """Compte de 300 € : le calcul donne < 0.01 -> on prend le minimum du broker."""
        risk = RiskManager(make_cfg(), FakeBridge())
        lot = await risk._compute_lot(make_signal(entry=4124.0, stop_loss=4117.0), 300.0)
        assert lot == 0.01

    async def test_fallback_no_sl(self):
        risk = RiskManager(make_cfg())
        lot = await risk._compute_lot(make_signal(entry=None, stop_loss=None), 10000.0)
        assert lot == 0.01

    async def test_without_bridge_is_capped(self):
        risk = RiskManager(make_cfg())
        lot = await risk._compute_lot(make_signal(), 10_000_000.0)
        assert lot <= 1.0


class TestMargin:
    async def test_lot_reduced_when_margin_too_low(self):
        # lot voulu 0.84 -> marge 1680 ; marge libre 1000 -> utilisable 800 -> 0.4 lot
        risk = RiskManager(make_cfg(), FakeBridge(free_margin=1000.0))
        res = await risk.check(make_signal(entry=4124.0, stop_loss=4117.0), 50000.0, 5.0, 4124.0)
        assert res.approved is True and res.lot_size == 0.4

    async def test_refused_when_even_min_lot_impossible(self):
        risk = RiskManager(make_cfg(), FakeBridge(free_margin=10.0))
        res = await risk.check(make_signal(entry=4124.0, stop_loss=4117.0), 50000.0, 5.0, 4124.0)
        assert res.approved is False and "Marge" in res.reason

    async def test_margin_ok_keeps_lot(self):
        risk = RiskManager(make_cfg(), FakeBridge(free_margin=50000.0))
        res = await risk.check(make_signal(entry=4124.0, stop_loss=4117.0), 50000.0, 5.0, 4124.0)
        assert res.approved is True and res.lot_size == 0.84
