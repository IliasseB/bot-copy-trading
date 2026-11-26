"""Ordres en attente trop vieux → annulés ; positions ouvertes jamais touchées."""
from execution.mt5_bridge import MT5Bridge
from execution.trade_executor import TradeExecutor


class Fake:
    def __init__(self, now, orders, positions=None):
        self.now, self.orders, self.positions, self.cancelled = now, orders, positions or {}, []

    async def get_active_state(self):
        return {"positions": dict(self.positions), "orders": dict(self.orders)}

    async def get_server_time(self, symbol="XAUUSD"):
        return self.now

    async def cancel_order(self, t):
        self.cancelled.append(t); self.orders.pop(t, None); return True


def order(age_min, comment="TG#1_TP1", magic=MT5Bridge.BOT_MAGIC, now=100_000):
    return {"comment": comment, "magic": magic, "symbol": "XAUUSD", "time_setup": now - age_min * 60}


async def test_ordre_trop_vieux_annule():
    mt5 = Fake(100_000, {1: order(45), 2: order(10)})
    ex = TradeExecutor(mt5, risk=None)
    assert await ex.expire_pending_orders(30) == 1
    assert mt5.cancelled == [1]


async def test_desactive_par_defaut():
    mt5 = Fake(100_000, {1: order(999)})
    assert await TradeExecutor(mt5, risk=None).expire_pending_orders(0) == 0


async def test_ordre_manuel_jamais_touche():
    mt5 = Fake(100_000, {1: order(999, comment="mon ordre", magic=0)})
    assert await TradeExecutor(mt5, risk=None).expire_pending_orders(30) == 0
