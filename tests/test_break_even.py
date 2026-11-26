"""Tests du passage en Break-Even (BE) quand TP1 est touché."""
import pytest
from execution.trade_executor import TradeExecutor
from execution.mt5_bridge import MT5Bridge


class FakeMT5:
    def __init__(self):
        self.positions, self.orders, self.reasons = {}, {}, {}
        self.modified, self.fail_next = [], set()

    async def get_active_state(self):
        return {"positions": self.positions, "orders": self.orders}

    async def get_close_reason(self, ticket):
        return self.reasons.get(ticket)

    async def modify_position_sltp(self, ticket, sl=None, tp=None):
        if ticket in self.fail_next:
            self.fail_next.discard(ticket)
            return False
        self.modified.append((ticket, sl))
        self.positions[ticket]["sl"] = sl
        return True

    async def get_symbol_trade_info(self, symbol):
        return {"tick_size": 0.01}


def pos(price, direction="BUY", sl=4100.0, comment="", magic=MT5Bridge.BOT_MAGIC):
    return {"price_open": price, "sl": sl, "tp": 0.0, "type": direction,
            "symbol": "XAUUSD", "comment": comment, "magic": magic}


def track(ex, tp1, others):
    ex._tracked_trades.append({"symbol": "XAUUSD", "direction": "BUY", "entry_price": 4110.0,
        "tp1": 4120.0, "tp1_ticket": tp1, "other_tickets": others,
        "be_triggered": False, "be_done": set(), "tick_size": 0.01})


@pytest.mark.asyncio
async def test_tp1_touche_tp2_tp3_en_be():
    mt5 = FakeMT5(); ex = TradeExecutor(mt5, risk=None)
    mt5.positions = {1: pos(4110.0), 2: pos(4110.2), 3: pos(4110.1)}
    track(ex, 1, [2, 3])
    await ex.monitor_and_adjust_be()
    assert mt5.modified == []                     # TP1 encore ouvert → rien
    del mt5.positions[1]; mt5.reasons[1] = "TP"    # TP1 touché
    await ex.monitor_and_adjust_be()
    assert sorted(mt5.modified) == [(2, 4110.2), (3, 4110.1)]   # BE au vrai prix d'entrée de chaque jambe


@pytest.mark.asyncio
async def test_offset_be_sell():
    mt5 = FakeMT5(); ex = TradeExecutor(mt5, risk=None, be_offset_points=10)
    mt5.positions = {2: pos(4110.0, "SELL", sl=4120.0)}
    mt5.reasons[1] = "TP"; track(ex, 1, [2])
    await ex.monitor_and_adjust_be()
    assert mt5.modified == [(2, 4109.9)]          # SELL : entrée - 0.10$


@pytest.mark.asyncio
async def test_tp1_au_sl_pas_de_be():
    mt5 = FakeMT5(); ex = TradeExecutor(mt5, risk=None)
    mt5.positions = {2: pos(4110.0)}; mt5.reasons[1] = "SL"; track(ex, 1, [2])
    await ex.monitor_and_adjust_be()
    assert mt5.modified == []


@pytest.mark.asyncio
async def test_pending_pas_encore_rempli_pas_de_be():
    mt5 = FakeMT5(); ex = TradeExecutor(mt5, risk=None)
    mt5.orders = {1: {}, 2: {}, 3: {}}; track(ex, 1, [2, 3])
    await ex.monitor_and_adjust_be()
    assert mt5.modified == [] and len(ex._tracked_trades) == 1   # toujours suivi


@pytest.mark.asyncio
async def test_echec_modif_retente():
    mt5 = FakeMT5(); ex = TradeExecutor(mt5, risk=None)
    mt5.positions = {2: pos(4110.0)}; mt5.reasons[1] = "TP"; mt5.fail_next = {2}
    track(ex, 1, [2])
    await ex.monitor_and_adjust_be(); assert mt5.modified == []
    await ex.monitor_and_adjust_be(); assert mt5.modified == [(2, 4110.0)]
    await ex.monitor_and_adjust_be(); assert len(mt5.modified) == 1   # pas refait


@pytest.mark.asyncio
async def test_recuperation_apres_redemarrage():
    mt5 = FakeMT5(); ex = TradeExecutor(mt5, risk=None)
    # TP1 déjà fermé pendant que le bot était éteint ; TP2 et OPEN encore là
    mt5.positions = {2: pos(4110.0, comment="TG#55_TP2"), 3: pos(4110.0, comment="TG#55_OPEN"),
                     9: pos(4000.0, comment="manuel", magic=0)}
    await ex.recover_tracked_trades()
    await ex.monitor_and_adjust_be()
    assert sorted(mt5.modified) == [(2, 4110.0), (3, 4110.0)]
