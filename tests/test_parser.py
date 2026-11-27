"""
tests/test_parser.py - Unit tests for regex and signal parser.
"""
import pytest

from parser.regex_parser import RegexParser
from parser.signal_model import Direction


@pytest.fixture
def parser():
    return RegexParser()


class TestRegexParser:

    def test_buy_gold_full(self, parser):
        text = "BUY GOLD 3350 SL 3340 TP 3365"
        signal, conf = parser.parse(text, msg_id=1, chat_id=100)
        assert signal is not None
        assert signal.direction == Direction.BUY
        assert signal.symbol == "XAUUSD"
        assert signal.stop_loss == 3340.0
        assert 3365.0 in signal.take_profits
        assert conf >= 0.7

    def test_sell_xauusd_with_keywords(self, parser):
        text = "SELL XAUUSD NOW PE: 3348 SL: 3355 TP1: 3340 TP2: 3330"
        signal, conf = parser.parse(text, msg_id=2, chat_id=100)
        assert signal is not None
        assert signal.direction == Direction.SELL
        assert signal.symbol == "XAUUSD"
        assert signal.entry == 3348.0
        assert signal.stop_loss == 3355.0
        assert 3340.0 in signal.take_profits
        assert 3330.0 in signal.take_profits

    def test_buy_xauusd_minimal(self, parser):
        text = "BUY XAUUSD"
        signal, conf = parser.parse(text, msg_id=3, chat_id=100)
        assert signal is not None
        assert signal.direction == Direction.BUY
        assert signal.symbol == "XAUUSD"

    def test_pending_sell_gold(self, parser):
        text = "PENDING SELL GOLD @3360 SL 3370 TP 3340"
        signal, conf = parser.parse(text, msg_id=4, chat_id=100)
        assert signal is not None
        assert signal.direction == Direction.SELL
        assert signal.symbol == "XAUUSD"
        assert signal.entry == 3360.0

    def test_emoji_formatting(self, parser):
        text = "🟢 BUY XAU/USD\n📍Entry: 3350\n🛑 SL: 3340\n🎯 TP: 3365"
        signal, conf = parser.parse(text, msg_id=5, chat_id=100)
        assert signal is not None
        assert signal.direction == Direction.BUY
        assert signal.symbol == "XAUUSD"

    def test_noise_message_ignored(self, parser):
        text = "Thanks everyone! Great call yesterday 👍"
        signal, conf = parser.parse(text, msg_id=6, chat_id=100)
        assert signal is None
        assert conf == 0.0

    def test_discussion_ignored(self, parser):
        text = "lol that was a good profit"
        signal, conf = parser.parse(text, msg_id=7, chat_id=100)
        assert signal is None

    def test_multiple_tps(self, parser):
        text = "BUY XAUUSD ENTRY 3350 SL 3330 TP1 3365 TP2 3380 TP3 3400"
        signal, conf = parser.parse(text, msg_id=8, chat_id=100)
        assert signal is not None
        assert len(signal.take_profits) == 3

    def test_lowercase_signal(self, parser):
        text = "buy gold sl 3340 tp 3365"
        signal, conf = parser.parse(text, msg_id=9, chat_id=100)
        assert signal is not None
        assert signal.direction == Direction.BUY

    def test_duplicate_not_parsed_twice(self):
        """Dedup is tested via SignalParser integration test."""
        pass


class TestSignalModel:

    def test_buy_sl_above_entry_raises(self):
        from pydantic import ValidationError

        from parser.signal_model import Direction, TradingSignal
        with pytest.raises(ValidationError, match="SL"):
            TradingSignal(
                telegram_msg_id=1,
                telegram_chat_id=1,
                raw_text="test",
                direction=Direction.BUY,
                symbol="XAUUSD",
                entry=3350.0,
                stop_loss=3360.0,  # SL above entry for BUY → invalid
                take_profits=[3370.0],
                confidence=0.9,
            )

    def test_latency_properties(self):
        import time

        from parser.signal_model import Direction, TradingSignal
        t0 = time.time_ns()
        sig = TradingSignal(
            telegram_msg_id=1,
            telegram_chat_id=1,
            raw_text="BUY GOLD",
            direction=Direction.BUY,
            symbol="XAUUSD",
            confidence=0.5,
            received_at_ns=t0,
            parsed_at_ns=t0 + 500_000,  # 0.5ms later
        )
        assert sig.parse_latency_ms is not None
        assert sig.parse_latency_ms == pytest.approx(0.5, abs=0.01)
