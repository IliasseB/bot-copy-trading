"""
database/db.py
PostgreSQL schema + asyncpg-based async writer/reader.
Uses SQLAlchemy Core (not ORM) for raw speed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from utils.logger import get_logger

log = get_logger("database")

# ── DDL ───────────────────────────────────────────────────────────────────────
CREATE_SIGNALS_TABLE = """
CREATE TABLE IF NOT EXISTS signals (
    id              TEXT PRIMARY KEY,
    telegram_msg_id BIGINT NOT NULL,
    telegram_chat_id BIGINT NOT NULL,
    raw_text        TEXT,
    direction       TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    order_type      TEXT,
    entry           DOUBLE PRECISION,
    stop_loss       DOUBLE PRECISION,
    take_profits    JSONB,
    confidence      REAL,
    parser_used     TEXT,
    status          TEXT,
    ticket          BIGINT,
    filled_price    DOUBLE PRECISION,
    lot_size        DOUBLE PRECISION,
    error_message   TEXT,
    received_at     TIMESTAMPTZ,
    parsed_at       TIMESTAMPTZ,
    executed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at DESC);
"""

CREATE_AUDIT_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    event       TEXT NOT NULL,
    payload     JSONB,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
"""


# ── Connection pool ────────────────────────────────────────────────────────────

class Database:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=2,
            max_size=10,
            command_timeout=10,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_SIGNALS_TABLE)
            await conn.execute(CREATE_AUDIT_TABLE)
        log.info("db_connected")

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            log.info("db_disconnected")

    # ── Signal persistence ────────────────────────────────────────────────────

    async def save_signal(self, signal) -> None:
        """Upsert a TradingSignal record."""
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO signals (
                        id, telegram_msg_id, telegram_chat_id, raw_text,
                        direction, symbol, order_type, entry, stop_loss,
                        take_profits, confidence, parser_used, status,
                        ticket, filled_price, lot_size, error_message,
                        received_at, parsed_at, executed_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
                    ON CONFLICT (id) DO UPDATE SET
                        status        = EXCLUDED.status,
                        ticket        = EXCLUDED.ticket,
                        filled_price  = EXCLUDED.filled_price,
                        lot_size      = EXCLUDED.lot_size,
                        error_message = EXCLUDED.error_message,
                        executed_at   = EXCLUDED.executed_at
                    """,
                    signal.id,
                    signal.telegram_msg_id,
                    signal.telegram_chat_id,
                    signal.raw_text,
                    signal.direction,
                    signal.symbol,
                    signal.order_type,
                    signal.entry,
                    signal.stop_loss,
                    json.dumps(signal.take_profits),
                    signal.confidence,
                    signal.parser_used,
                    signal.status,
                    signal.ticket,
                    signal.filled_price,
                    signal.lot_size,
                    signal.error_message,
                    _ns_to_dt(signal.received_at_ns),
                    _ns_to_dt(signal.parsed_at_ns),
                    _ns_to_dt(signal.executed_at_ns),
                )
        except Exception as e:
            log.error("db_save_failed", signal_id=signal.id, error=str(e))

    async def audit(self, event: str, payload: dict) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO audit_log (event, payload) VALUES ($1, $2)",
                    event,
                    json.dumps(payload),
                )
        except Exception as e:
            log.error("audit_failed", event=event, error=str(e))

    async def get_daily_stats(self) -> dict:
        if not self._pool:
            return {}
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE status='EXECUTED') AS executed,
                    COUNT(*) FILTER (WHERE status='REJECTED') AS rejected,
                    COUNT(*) FILTER (WHERE status='FAILED')   AS failed,
                    AVG(EXTRACT(EPOCH FROM (executed_at - received_at)) * 1000)
                        FILTER (WHERE status='EXECUTED') AS avg_exec_ms
                FROM signals
                WHERE created_at >= CURRENT_DATE
            """)
            return dict(row) if row else {}


def _ns_to_dt(ns: Optional[int]) -> Optional[datetime]:
    if ns is None:
        return None
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc)
