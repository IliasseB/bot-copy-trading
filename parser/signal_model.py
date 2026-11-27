"""
parser/signal_model.py - Modèle signal avec support intervalle d'entrée.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Direction(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET  = "MARKET"
    LIMIT   = "LIMIT"
    STOP    = "STOP"
    PENDING = "PENDING"


class SignalStatus(str, Enum):
    PARSED    = "PARSED"
    VALIDATED = "VALIDATED"
    EXECUTING = "EXECUTING"
    EXECUTED  = "EXECUTED"
    REJECTED  = "REJECTED"
    FAILED    = "FAILED"


class TradingSignal(BaseModel):
    # ── Identité ─────────────────────────────────────────────────────────
    id:               str   = Field(default_factory=lambda: str(uuid.uuid4()))
    telegram_msg_id:  int
    telegram_chat_id: int
    raw_text:         str

    # ── Signal ───────────────────────────────────────────────────────────
    direction:    Direction
    symbol:       str
    order_type:   OrderType       = OrderType.MARKET
    entry:        Optional[float] = None   # Prix d'entrée (milieu ou meilleur bord)
    entry_low:    Optional[float] = None   # Bas de la zone  ex: 4457
    entry_high:   Optional[float] = None   # Haut de la zone ex: 4460
    stop_loss:    Optional[float] = None
    take_profits: List[Optional[float]] = Field(default_factory=list)

    # ── Meta ─────────────────────────────────────────────────────────────
    confidence:  float       = Field(ge=0.0, le=1.0)
    parser_used: str         = "regex"
    status:      SignalStatus = SignalStatus.PARSED

    # ── Timing ───────────────────────────────────────────────────────────
    received_at_ns:  int           = Field(default_factory=time.time_ns)
    parsed_at_ns:    Optional[int] = None
    executed_at_ns:  Optional[int] = None

    # ── Résultat exécution ────────────────────────────────────────────────
    ticket:        Optional[int]   = None
    filled_price:  Optional[float] = None
    lot_size:      Optional[float] = None
    error_message: Optional[str]   = None

    model_config = ConfigDict(use_enum_values=True)

    @field_validator("symbol")
    @classmethod
    def symbol_upper(cls, v: str) -> str:
        return v.upper().strip()

    @model_validator(mode="after")
    def _niveaux_coherents(self):
        """BUY : SL sous l'entrée et TP au-dessus ; SELL : l'inverse. Sinon = faute de frappe → refusé."""
        levels = [x for x in (self.entry_low, self.entry_high, self.entry) if x]
        if not levels:
            return self
        buy = str(getattr(self.direction, "value", self.direction)) == "BUY"
        lo, hi = min(levels), max(levels)
        if self.stop_loss is not None:
            if buy and self.stop_loss >= lo:
                raise ValueError(f"BUY avec SL {self.stop_loss} au-dessus de l'entrée {lo}")
            if not buy and self.stop_loss <= hi:
                raise ValueError(f"SELL avec SL {self.stop_loss} sous l'entrée {hi}")
        for tp in self.take_profits:
            if tp is None:
                continue
            if buy and tp <= lo:
                raise ValueError(f"BUY avec TP {tp} sous l'entrée {lo}")
            if not buy and tp >= hi:
                raise ValueError(f"SELL avec TP {tp} au-dessus de l'entrée {hi}")
        return self

    @property
    def primary_tp(self) -> Optional[float]:
        for tp in self.take_profits:
            if tp is not None:
                return tp
        return None

    def is_price_in_zone(self, current_price: float) -> bool:
        """
        Vérifie si le prix actuel est dans la zone d'entrée.
        Si pas de zone définie, retourne True (market order direct).
        """
        if self.entry_low is None or self.entry_high is None:
            return True  # Pas de zone → market direct
        lo = min(self.entry_low, self.entry_high)
        hi = max(self.entry_low, self.entry_high)
        # Marge de 2 points au-dessus/en dessous de la zone
        margin = 2.0
        return (lo - margin) <= current_price <= (hi + margin)

    @property
    def parse_latency_ms(self) -> Optional[float]:
        if self.parsed_at_ns:
            return (self.parsed_at_ns - self.received_at_ns) / 1_000_000
        return None

    @property
    def execution_latency_ms(self) -> Optional[float]:
        if self.executed_at_ns:
            return (self.executed_at_ns - self.received_at_ns) / 1_000_000
        return None
