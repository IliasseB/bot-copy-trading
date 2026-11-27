"""
risk_management/risk_manager.py - Full risk gate.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Optional

from config import RiskConfig
from parser.signal_model import TradingSignal
from utils.logger import get_logger
from utils.trading_hours import is_trading_time

log = get_logger("risk")


@dataclass
class DailyStats:
    date_str: str = ""
    total_loss: float = 0.0
    total_profit: float = 0.0
    trades_opened: int = 0
    emergency_stopped: bool = False


@dataclass
class RiskCheckResult:
    approved: bool
    reason: str = ""
    lot_size: float = 0.0


def floor_to_step(value: float, step: float) -> float:
    """Arrondit VERS LE BAS au pas de volume du broker (jamais vers le haut)."""
    if step <= 0:
        return value
    return round(math.floor(value / step + 1e-9) * step, 8)


class RiskManager:
    def __init__(self, config: RiskConfig, mt5_bridge=None):
        self._cfg = config
        self._mt5 = mt5_bridge
        self._lock = asyncio.Lock()
        self._daily = DailyStats()
        self._open_trades: int = 0
        self._emergency_stop: bool = False

    async def check(
        self,
        signal: TradingSignal,
        account_balance: float,
        current_spread_points: float,
        current_price: Optional[float] = None,
    ) -> RiskCheckResult:
        async with self._lock:
            self._refresh_daily_stats()

            if self._emergency_stop:
                return RiskCheckResult(False, "EMERGENCY_STOP_ACTIVE")

            if self._daily.emergency_stopped:
                return RiskCheckResult(False, "DAILY_LOSS_LIMIT_HIT")

            hours, days = getattr(self._cfg, "trading_hours", ""), getattr(self._cfg, "trading_days", "")
            if (hours or days) and not is_trading_time(hours, days, self._now()):
                return RiskCheckResult(False, f"Hors horaires de trading (heures='{hours}' jours='{days}')")

            # Chiffres en direct depuis MT5 si possible (sinon compteurs internes).
            # Avant, ces compteurs n'étaient jamais remis à jour à la fermeture des trades :
            # la limite de trades simultanés bloquait tout après 2 signaux, et la perte
            # max du jour ne se déclenchait jamais.
            stats = None
            if self._mt5 is not None and hasattr(self._mt5, "get_bot_risk_stats"):
                stats = await self._mt5.get_bot_risk_stats()

            open_trades = stats["open_signals"] if stats else self._open_trades
            if open_trades >= self._cfg.max_simultaneous_trades:
                return RiskCheckResult(False, f"Max simultaneous trades reached ({open_trades}/{self._cfg.max_simultaneous_trades})")

            max_day = int(getattr(self._cfg, "max_trades_per_day", 0) or 0)
            if max_day > 0:
                done_today = stats.get("signals_today", self._daily.trades_opened) if stats else self._daily.trades_opened
                if done_today >= max_day:
                    return RiskCheckResult(False, f"Limite de {max_day} signal(aux) par jour atteinte ({done_today})")

            if current_spread_points > self._cfg.max_spread_points:
                return RiskCheckResult(False, f"Spread too wide: {current_spread_points} > {self._cfg.max_spread_points}")

            if stats:
                day_loss = max(0.0, -stats["pnl_today"])
                start_balance = account_balance - stats["pnl_today"]   # solde en début de journée
            else:
                day_loss = self._daily.total_loss
                start_balance = account_balance
            loss_pct = (day_loss / start_balance * 100) if start_balance else 0
            if self._cfg.max_daily_loss_percent > 0 and loss_pct >= self._cfg.max_daily_loss_percent:
                self._daily.emergency_stopped = True
                log.critical(f"Daily loss limit hit: {loss_pct:.2f}%")
                return RiskCheckResult(False, f"Daily loss limit hit: {loss_pct:.2f}%")

            lot = await self._compute_lot(signal, account_balance)
            if lot <= 0:
                return RiskCheckResult(False, "Lot size = 0")

            # Vérifie que la marge libre suffit ; réduit le lot si besoin, refuse sinon.
            lot, margin_reason = await self._fit_lot_to_margin(signal, lot, current_price)
            if lot <= 0:
                return RiskCheckResult(False, margin_reason or "Marge insuffisante")

            return RiskCheckResult(approved=True, lot_size=lot)

    async def _get_trade_info(self, symbol: str) -> dict:
        """Infos réelles du symbole chez le broker ; valeurs prudentes si indisponible."""
        info = {
            "tick_value": 1.0, "tick_size": 0.01,
            "volume_min": 0.01, "volume_max": 1.0, "volume_step": 0.01,
        }
        if self._mt5 is not None:
            try:
                real = await self._mt5.get_symbol_trade_info(symbol)
                info.update({k: real[k] for k in info if k in real})
            except Exception as e:
                log.warning(f"Infos symbole indisponibles ({e}) — valeurs par défaut prudentes utilisées")
        return info

    async def _compute_lot(self, signal: TradingSignal, account_balance: float) -> float:
        info = await self._get_trade_info(signal.symbol)
        tick_value, tick_size = info["tick_value"], info["tick_size"]
        vol_min, vol_max, vol_step = info["volume_min"], info["volume_max"], info["volume_step"]

        # Plafond dur : le plus petit entre le max du broker et RISK_MAX_LOT
        hard_cap = min(vol_max, self._cfg.max_lot)

        def _finalize(lot: float) -> float:
            lot = floor_to_step(lot, vol_step)
            lot = max(vol_min, min(lot, hard_cap))
            return round(lot, 2)

        # Mode "lot par TP" : 1 position de X lot pour CHAQUE TP du signal
        if getattr(self._cfg, "lot_per_tp", 0) > 0:
            n_tps = max(1, len(signal.take_profits or []))
            lot = _finalize(self._cfg.lot_per_tp * n_tps)
            log.info(f"Lot PAR TP: {self._cfg.lot_per_tp} × {n_tps} TP = {lot} (plafond={hard_cap})")
            return lot

        # ── Mode LOT FIXE : court-circuite tout le calcul ─────────────────
        if self._cfg.fixed_lot > 0:
            lot = _finalize(self._cfg.fixed_lot)
            log.info(f"Lot FIXE appliqué: {lot} (RISK_FIXED_LOT={self._cfg.fixed_lot}, plafond={hard_cap})")
            return lot

        if signal.entry is None or signal.stop_loss is None:
            lot = _finalize(self._cfg.default_lot_size)
            log.info(f"Pas d'entrée/SL exploitable — lot par défaut {lot}")
            return lot

        sl_distance = abs(signal.entry - signal.stop_loss)
        if sl_distance <= 0 or tick_size <= 0 or tick_value <= 0:
            return _finalize(self._cfg.default_lot_size)

        # Perte (en devise du compte) pour 1 lot si le SL est touché
        value_per_lot = (sl_distance / tick_size) * tick_value
        risk_amount = account_balance * (self._cfg.max_percent_per_trade / 100)
        raw_lot = risk_amount / value_per_lot
        lot = _finalize(raw_lot)

        real_risk = lot * value_per_lot
        log.info(
            f"Lot calculé: balance={account_balance:.2f} risque_voulu={risk_amount:.2f} "
            f"dist_SL={sl_distance:.2f} perte/lot={value_per_lot:.2f} "
            f"lot_brut={raw_lot:.4f} -> lot={lot} (risque réel≈{real_risk:.2f})"
        )
        if raw_lot < vol_min:
            log.warning(
                f"Lot minimum du broker ({vol_min}) > lot calculé ({raw_lot:.4f}) : "
                f"risque réel ≈ {real_risk:.2f} ({real_risk / account_balance * 100:.2f}% du compte) "
                f"au lieu de {self._cfg.max_percent_per_trade}%"
            )
        return lot

    async def _fit_lot_to_margin(self, signal: TradingSignal, lot: float, price: Optional[float]):
        """Retourne (lot_ajusté, raison_refus). Ne retente jamais un ordre impossible."""
        if self._mt5 is None:
            return lot, ""
        ref_price = price or signal.entry
        if not ref_price:
            return lot, ""
        try:
            direction = str(getattr(signal.direction, "value", signal.direction))  # str ou Enum
            m = await self._mt5.get_margin_info(signal.symbol, direction, lot, ref_price)
        except Exception as e:
            log.warning(f"Vérification de marge indisponible ({e}) — on continue sans")
            return lot, ""
        if not m or m.get("margin_required", 0) <= 0:
            return lot, ""

        required, free = m["margin_required"], m["free_margin"]
        usable = free * self._cfg.margin_safety_ratio
        if required <= usable:
            log.info(f"Marge OK: requise={required:.2f} libre={free:.2f}")
            return lot, ""

        info = await self._get_trade_info(signal.symbol)
        per_lot = required / lot
        fitted = floor_to_step(usable / per_lot, info["volume_step"])
        if fitted < info["volume_min"]:
            reason = (f"Marge insuffisante: requise={required:.2f} libre={free:.2f} "
                      f"(même le lot minimum {info['volume_min']} ne passe pas)")
            log.error(reason)
            return 0.0, reason
        log.warning(f"Marge insuffisante pour lot={lot} (requise={required:.2f}, libre={free:.2f}) — lot réduit à {fitted}")
        return round(fitted, 2), ""

    @staticmethod
    def _now():
        from datetime import datetime
        return datetime.now()

    def on_trade_opened(self) -> None:
        self._open_trades += 1
        self._refresh_daily_stats()
        self._daily.trades_opened += 1

    def on_trade_closed(self, pnl: float) -> None:
        self._open_trades = max(0, self._open_trades - 1)
        if pnl < 0:
            self._daily.total_loss += abs(pnl)
        else:
            self._daily.total_profit += pnl

    def trigger_emergency_stop(self, reason: str = "manual") -> None:
        self._emergency_stop = True
        log.critical(f"EMERGENCY STOP triggered: {reason}")

    def reset_emergency_stop(self) -> None:
        self._emergency_stop = False
        log.info("Emergency stop reset")

    def _refresh_daily_stats(self) -> None:
        from datetime import date
        today = date.today().isoformat()
        if self._daily.date_str != today:
            self._daily = DailyStats(date_str=today)

    def check_slippage(self, requested_price: float, filled_price: float, symbol: str) -> bool:
        slippage = abs(filled_price - requested_price) / 0.01
        if slippage > self._cfg.max_slippage_points:
            log.warning(f"Slippage exceeded — requested={requested_price} filled={filled_price} pts={slippage} {symbol}")
            return False
        return True
