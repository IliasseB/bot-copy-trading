"""
Scénario réel RTZ ACADEMY du 06/11 : signal avec faute de frappe (4166 au lieu de 4066),
corrigé par édition, puis réponse "modifier le SL à 62.50".
"""
import itertools
from types import SimpleNamespace

import pytest

from config import RiskConfig
from execution.mt5_bridge import MT5Bridge
from execution.trade_executor import TradeExecutor
from main import TradingBot
from parser.signal_parser import SignalParser
from parser.update_parser import parse_update, resolve_price
from risk_management.risk_manager import RiskManager

SIGNAL_TYPO = """**🚨****BUY XAUUSD**

**➡️****ENTRY : 4166 - 4168**

**🔴****SL : 4164**

**🟢****TP1: 4172**

**⚠️****Disclaimer : Il ne s’agit en aucun cas d’un conseil financier"""
SIGNAL_FIXED = SIGNAL_TYPO.replace("4166 - 4168", "4066 - 4069").replace("4164", "4064").replace("4172", "4072")
SIGNAL_3TP = SIGNAL_FIXED.replace("**🟢****TP1: 4072**", "**🟢****TP1: 4072**\n\nTP2: 4076\n\nTP3: 4080")


class FakeMT5:
    def __init__(self, price=4069.0):
        self.price = price
        self.positions, self.orders = {}, {}
        self.log = []
        self._t = itertools.count(1000)

    async def get_market_snapshot(self, symbol, direction):
        return {"balance": 500.0, "spread": 15.0, "price": self.price}

    async def get_symbol_trade_info(self, symbol):
        return {"tick_value": 1.0, "tick_size": 0.01, "volume_min": 0.01,
                "volume_max": 50.0, "volume_step": 0.01}

    async def get_margin_info(self, *a):
        return None

    async def send_market_order(self, symbol, direction, lot, sl, tp, comment, known_price=None):
        t = next(self._t)
        self.positions[t] = {"price_open": self.price, "sl": sl, "tp": tp, "type": str(direction.value if hasattr(direction, "value") else direction),
                             "symbol": symbol, "comment": comment, "magic": MT5Bridge.BOT_MAGIC}
        self.log.append(("market", t, sl, tp))
        return {"ticket": t, "filled_price": self.price}

    async def send_pending_order(self, symbol, direction, order_type, lot, entry, sl, tp, comment):
        t = next(self._t)
        self.orders[t] = {"price_open": entry, "sl": sl, "tp": tp, "type": str(direction.value if hasattr(direction, "value") else direction),
                          "symbol": symbol, "comment": comment, "magic": MT5Bridge.BOT_MAGIC}
        self.log.append(("pending", t, entry, sl, tp))
        return {"ticket": t, "filled_price": entry}

    async def get_active_state(self):
        return {"positions": dict(self.positions), "orders": dict(self.orders)}

    async def cancel_order(self, t):
        self.log.append(("cancel", t)); self.orders.pop(t, None); return True

    async def modify_pending_order(self, t, price=None, sl=None, tp=None):
        if sl is not None: self.orders[t]["sl"] = sl
        if tp is not None: self.orders[t]["tp"] = tp
        self.log.append(("modify_order", t, sl, tp)); return True

    async def modify_position_sltp(self, t, sl=None, tp=None):
        if sl is not None: self.positions[t]["sl"] = sl
        if tp is not None: self.positions[t]["tp"] = tp
        self.log.append(("modify_pos", t, sl, tp)); return True

    async def close_position(self, t):
        self.log.append(("close", t)); self.positions.pop(t, None); return True


def make_bot(mt5, lot=0.01):
    # lot_per_tp=0 explicite : le test ne doit pas dépendre du .env réel (RISK_LOT_PER_TP)
    risk = RiskManager(RiskConfig(fixed_lot=lot, lot_per_tp=0.0, max_lot=0.03, max_spread_points=50), mt5)
    bot = TradingBot.__new__(TradingBot)
    bot._cfg = SimpleNamespace(bot=SimpleNamespace(max_edit_age_seconds=900, update_no_reply_fallback=True))
    bot._parser = SignalParser()
    bot._executor = TradeExecutor(mt5, risk, confidence_threshold=0.45)
    bot._db = None
    bot._pending_orders = {}
    bot._reply_root = {}
    bot._last_signal_msg_id = None
    return bot


@pytest.mark.asyncio
async def test_scenario_reel_faute_de_frappe_puis_correction_puis_sl():
    mt5 = FakeMT5(price=4069.0)
    bot = make_bot(mt5)

    # 11:36:11 signal avec 4166 alors que l'or est à 4069 → REFUSÉ (faute de frappe)
    await bot._on_telegram_message(SIGNAL_TYPO, 10511, 1, False)
    assert mt5.log == []

    # 11:36:21 première édition, toujours fausse → toujours refusé
    await bot._on_telegram_message(SIGNAL_TYPO.replace("4168", "4169"), 10511, 1, True, age_s=10)
    assert mt5.log == []

    # 11:38:26 correction 4066-4069 → prix 4069 dans la zone → ACHAT AU MARCHÉ
    await bot._on_telegram_message(SIGNAL_FIXED, 10511, 1, True, age_s=135)
    assert [e[0] for e in mt5.log] == ["market"]
    ticket = mt5.log[0][1]
    assert mt5.positions[ticket]["sl"] == 4064.0 and mt5.positions[ticket]["tp"] == 4072.0

    # 11:39 "Trade update : gérez bien vos lots" (réponse) → rien
    await bot._on_telegram_message("📊**__ Trade update : gérez bien vos lots, l’équipe ! Ça reste un petit scalp__**",
                                   10512, 1, False, reply_to=10511)
    assert len(mt5.log) == 1

    # 11:46 "On peut légèrement modifier le SL à 62.50" → SL 4062.50
    await bot._on_telegram_message("On peut légèrement modifier le SL à 62.50", 10513, 1, False, reply_to=10511)
    assert mt5.positions[ticket]["sl"] == 4062.5


@pytest.mark.asyncio
async def test_edit_ordre_en_attente_annule_et_replace():
    mt5 = FakeMT5(price=4080.0)              # prix au-dessus de la zone → ordre en attente
    bot = make_bot(mt5)
    await bot._on_telegram_message(SIGNAL_FIXED, 20, 1, False)
    assert mt5.log[0][0] == "pending" and mt5.log[0][2] == 4066.0
    await bot._on_telegram_message(SIGNAL_FIXED.replace("4066 - 4069", "4070 - 4073"), 20, 1, True, age_s=60)
    kinds = [e[0] for e in mt5.log]
    assert kinds == ["pending", "cancel", "pending"] and mt5.log[2][2] == 4070.0


@pytest.mark.asyncio
async def test_edit_sans_changement_ne_touche_rien():
    mt5 = FakeMT5(price=4080.0)
    bot = make_bot(mt5)
    await bot._on_telegram_message(SIGNAL_FIXED, 21, 1, False)
    await bot._on_telegram_message(SIGNAL_FIXED + "\n\nTP1 ✅", 21, 1, True, age_s=300)
    assert [e[0] for e in mt5.log] == ["pending"]


@pytest.mark.asyncio
async def test_edit_position_ouverte_met_a_jour_sl_tp():
    mt5 = FakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.03)
    await bot._on_telegram_message(SIGNAL_3TP, 30, 1, False)
    assert len(mt5.positions) == 3
    await bot._on_telegram_message(SIGNAL_3TP.replace("4064", "4063").replace("4080", "4085"), 30, 1, True, age_s=60)
    sls = sorted(p["sl"] for p in mt5.positions.values())
    tps = sorted(p["tp"] for p in mt5.positions.values())
    assert sls == [4063.0] * 3 and tps == [4072.0, 4076.0, 4085.0]


@pytest.mark.asyncio
async def test_trade_termine_puis_edit_ne_rouvre_pas():
    mt5 = FakeMT5(price=4068.0)
    bot = make_bot(mt5)
    await bot._on_telegram_message(SIGNAL_FIXED, 40, 1, False)
    mt5.positions.clear()                                # TP touché, plus rien d'ouvert
    await bot._on_telegram_message(SIGNAL_FIXED + "\n✅ TP1 +40 pips", 40, 1, True, age_s=600)
    assert [e[0] for e in mt5.log] == ["market"]


@pytest.mark.asyncio
async def test_reponses_be_et_fermeture():
    mt5 = FakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.03)
    await bot._on_telegram_message(SIGNAL_3TP, 50, 1, False)
    await bot._on_telegram_message("TP1 touché ✅ mettez le SL au BE", 51, 1, False, reply_to=50)
    assert all(p["sl"] == 4068.0 for p in mt5.positions.values())
    await bot._on_telegram_message("Ne fermez pas encore", 52, 1, False, reply_to=50)
    assert len(mt5.positions) == 3
    await bot._on_telegram_message("Fermez le trade maintenant", 53, 1, False, reply_to=50)
    assert mt5.positions == {}


def test_parser_messages_reels():
    assert [str(c) for c in parse_update("On peut légèrement modifier le SL à 62.50")] == ["SL→62.5"]
    assert parse_update("📊 Trade update : gérez bien vos lots, l’équipe !") == []
    # "TP1 touché" = encaisser la jambe TP1 si elle est encore ouverte en profit (spread différent)
    assert [str(c) for c in parse_update("TP1 touché ✅ +40 pips")] == ["TP_HIT TP1"]
    assert resolve_price(62.5, 4066) == 4062.5
    assert resolve_price(4062.5, 4066) == 4062.5


@pytest.mark.asyncio
async def test_002_lot_avec_3_tp_prend_tp1_et_tp2():
    mt5 = FakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIGNAL_3TP, 60, 1, False)
    assert sorted(p["tp"] for p in mt5.positions.values()) == [4072.0, 4076.0]
    assert all(e[0] == "market" for e in mt5.log)


@pytest.mark.asyncio
async def test_lot_par_tp_ouvre_une_position_par_tp():
    mt5 = FakeMT5(price=4068.0)
    risk = RiskManager(RiskConfig(lot_per_tp=0.01, max_lot=0.05, max_spread_points=50), mt5)
    bot = make_bot(mt5); bot._executor._risk = risk
    two_tp = SIGNAL_FIXED.replace("**🟢****TP1: 4072**", "**🟢****TP1: 4072**\n\nTP2: 4076")
    await bot._on_telegram_message(two_tp, 61, 1, False)
    assert sorted(p["tp"] for p in mt5.positions.values()) == [4072.0, 4076.0]
