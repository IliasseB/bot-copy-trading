"""
utils/logger.py - Structured logging simplifié et robuste.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging(level: str = "INFO", log_file: str = "bot.log") -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / log_file, encoding="utf-8"),
    ]

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        handlers=handlers,
        level=log_level,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


class TradeLogger:
    def __init__(self, name: str = "trade"):
        self._log = get_logger(name)

    def signal_received(self, msg_id: int, chat_id: int, text_snippet: str) -> None:
        self._log.info(f"[SIGNAL_RECEIVED] msg_id={msg_id} chat_id={chat_id} text={text_snippet[:80]}")

    def signal_parsed(self, msg_id, direction, symbol, entry, sl, tps, confidence, parse_duration_ms) -> None:
        self._log.info(
            f"[SIGNAL_PARSED] msg_id={msg_id} {direction} {symbol} "
            f"entry={entry} sl={sl} tps={tps} conf={confidence:.2f} parse_ms={parse_duration_ms:.1f}"
        )

    def signal_rejected(self, msg_id: int, reason: str) -> None:
        self._log.warning(f"[SIGNAL_REJECTED] msg_id={msg_id} reason={reason}")

    def order_sent(self, msg_id, ticket, symbol, direction, lot, price, sl, tp, execution_ms) -> None:
        self._log.info(
            f"[ORDER_EXECUTED] msg_id={msg_id} ticket={ticket} {direction} {symbol} "
            f"lot={lot} price={price} sl={sl} tp={tp} exec_ms={execution_ms:.1f}"
        )

    def order_failed(self, msg_id: int, error: str, retcode: int | None = None) -> None:
        self._log.error(f"[ORDER_FAILED] msg_id={msg_id} error={error} retcode={retcode}")

    def slippage(self, msg_id: int, requested: float, filled: float, points: float) -> None:
        self._log.warning(f"[SLIPPAGE] msg_id={msg_id} requested={requested} filled={filled} pts={points}")

    def emergency_stop(self, reason: str) -> None:
        self._log.critical(f"[EMERGENCY_STOP] reason={reason}")
