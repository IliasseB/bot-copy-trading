"""
config.py - Centralized configuration using Pydantic Settings.
Loaded once at startup, immutable at runtime.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Dict, List

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_", env_file=".env", extra="ignore")

    api_id: int = Field(..., description="Telegram API ID from my.telegram.org")
    api_hash: SecretStr = Field(..., description="Telegram API Hash from my.telegram.org (jamais affiché)")
    phone: str = Field(..., description="Phone number with country code")
    session_name: str = Field(default="trading_bot")
    group_ids: str = Field(..., description="Comma-separated group IDs")

    @property
    def group_id_list(self) -> List[int | str]:
        ids = []
        for g in self.group_ids.split(","):
            g = g.strip()
            if g.lstrip("-").isdigit():
                ids.append(int(g))
            else:
                ids.append(g)
        return ids


class MT5Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MT5_", env_file=".env", extra="ignore")

    login: int = Field(..., description="MT5 account login")
    password: SecretStr = Field(..., description="MT5 account password (jamais affiché)")
    server: str = Field(..., description="MT5 broker server")
    path: str = Field(default="", description="Path to terminal64.exe")
    timeout: int = Field(default=60000, description="MT5 connection timeout ms")
    allow_real: bool = Field(
        default=False,
        description="Sécurité : doit valoir true pour que le bot accepte de trader sur un compte RÉEL",
    )


class RiskConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_", env_file=".env", extra="ignore")

    max_percent_per_trade: float = Field(default=1.0)
    max_daily_loss_percent: float = Field(default=0.0, description="Perte max du jour en % (0 = désactivé)")
    max_simultaneous_trades: int = Field(default=5)
    max_spread_points: int = Field(default=30)
    max_slippage_points: int = Field(default=20)
    default_lot_size: float = Field(default=0.01)
    fixed_lot: float = Field(default=0.0, description="Lot fixe (0 = calcul auto selon le risque)")
    lot_per_tp: float = Field(
        default=0.0,
        description="Lot PAR TP (ex: 0.01 → signal à 2 TP = 2 × 0.01). 0 = désactivé, on utilise RISK_FIXED_LOT",
    )
    max_lot: float = Field(default=1.0, description="Plafond dur du lot, quel que soit le calcul")
    margin_safety_ratio: float = Field(default=0.8, description="Part de la marge libre utilisable")
    trading_hours: str = Field(
        default="", description='Heures où le bot ouvre des trades, ex "08:00-22:00" (vide = toujours)',
    )
    trading_days: str = Field(default="", description='Jours de trading, ex "1-5" = lundi→vendredi (vide = tous)')
    max_trades_per_day: int = Field(default=0, description="Nombre max de signaux tradés par jour (0 = illimité)")
    max_entry_distance_percent: float = Field(
        default=1.0,
        description="Refuse un signal dont l'entrée est à plus de X % du prix actuel (faute de frappe)",
    )


class DatabaseConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(default="postgresql://user:pass@localhost:5432/trading_bot")
    redis_url: str = Field(default="redis://localhost:6379/0")


class BotConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BOT_", env_file=".env", extra="ignore")

    env: str = Field(default="production")
    log_level: str = Field(default="INFO")
    max_signal_age_seconds: int = Field(default=30)
    dedup_window_seconds: int = Field(default=60)
    signal_confidence_threshold: float = Field(default=0.7)
    max_edit_age_seconds: int = Field(
        default=900,
        description="Un signal modifié plus de X s après sa publication n'ouvre plus de NOUVEAU trade",
    )

    # ── Messages de gestion du canal (BE, TP1 touché, clôtures...) ────────
    update_partial_close: bool = Field(
        default=True, description="Appliquer 'SÉCURISEZ 50%' / 'CLÔTUREZ 75%' (clôture partielle) sur MT5",
    )
    update_no_reply_fallback: bool = Field(
        default=True,
        description="Un ordre clair posté SANS 'répondre' s'applique au dernier signal (s'il a des trades ouverts)",
    )
    notify: bool = Field(
        default=False, description="Notifications du bot dans tes Messages enregistrés Telegram",
    )
    pending_expiry_minutes: float = Field(
        default=0, description="Annule un ordre en attente non déclenché après X minutes (0 = jamais)",
    )
    update_max_distance_percent: float = Field(
        default=3.0, description="SL/TP reçu par message à plus de X % de l'entrée = faute de frappe → ignoré",
    )

    symbol_map: str = Field(
        default='{"GOLD":"XAUUSD","XAU":"XAUUSD","XAUUSD":"XAUUSD"}',
        description="JSON string mapping aliases to broker symbols"
    )

    @property
    def symbol_mapping(self) -> Dict[str, str]:
        return json.loads(self.symbol_map)


class ExecutionConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EXECUTION_", env_file=".env", extra="ignore")

    timeout_ms: int = Field(default=5000)
    reconnect_delay_seconds: int = Field(default=5)
    reconnect_max_attempts: int = Field(default=10)
    be_offset_points: int = Field(
        default=0, description="BE à entrée + X points (or : 10 points = 0.10$) pour couvrir la commission"
    )


class AppConfig:
    """Aggregate config — single source of truth."""

    def __init__(self):
        self.telegram = TelegramConfig()
        self.mt5 = MT5Config()
        self.risk = RiskConfig()
        self.database = DatabaseConfig()
        self.bot = BotConfig()
        self.execution = ExecutionConfig()

    @property
    def is_production(self) -> bool:
        return self.bot.env == "production"

    @property
    def is_development(self) -> bool:
        return self.bot.env == "development"


def reveal(value) -> str:
    """Valeur en clair d'un SecretStr (ou d'une chaîne simple), à n'utiliser qu'au moment de se connecter."""
    return value.get_secret_value() if hasattr(value, "get_secret_value") else value


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return singleton config instance."""
    return AppConfig()
