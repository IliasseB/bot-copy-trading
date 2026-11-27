from parser.signal_model import Direction, TradingSignal
from execution.trade_executor import TradeExecutor


def sig(lo, hi):
    return TradingSignal(telegram_msg_id=1, telegram_chat_id=1, raw_text="x", direction=Direction.BUY, symbol="XAUUSD",
                         entry=lo, entry_low=lo, entry_high=hi, stop_loss=lo - 4, take_profits=[hi + 5], confidence=1)


def test_rempli_dans_la_zone_pas_de_glissement():
    # cas réel du 25/11 : zone 3484-3486, rempli à 3484.62 → plus de faux "Slippage exceeded"
    assert TradeExecutor._slippage_reference(sig(3484.0, 3486.0), 3484.62) is None


def test_rempli_hors_zone_mesure_au_bord_le_plus_proche():
    assert TradeExecutor._slippage_reference(sig(3484.0, 3486.0), 3487.5) == 3486.0
    assert TradeExecutor._slippage_reference(sig(3484.0, 3486.0), 3483.0) == 3484.0
