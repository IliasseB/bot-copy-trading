"""
utils/config_check.py - Repère les réglages incohérents du .env AVANT qu'ils ne surprennent en réel.
"""
from __future__ import annotations

from typing import List, Tuple

from utils.trading_hours import parse_days, parse_ranges

Level = str   # "ERREUR" | "ATTENTION" | "INFO"


def check_config(cfg) -> List[Tuple[Level, str]]:
    out: List[Tuple[Level, str]] = []
    r, b = cfg.risk, cfg.bot

    if getattr(r, "lot_per_tp", 0) > 0:
        if r.max_lot < 2 * r.lot_per_tp - 1e-9:
            out.append(("ATTENTION", f"RISK_MAX_LOT={r.max_lot} < 2 × RISK_LOT_PER_TP={r.lot_per_tp} : "
                                     f"la position TP2 ne sera jamais ouverte"))
        if r.fixed_lot > 0:
            out.append(("INFO", "RISK_LOT_PER_TP est actif : RISK_FIXED_LOT est ignoré"))
    if r.max_daily_loss_percent <= 0:
        out.append(("ATTENTION", "RISK_MAX_DAILY_LOSS_PERCENT=0 : aucun arrêt automatique en cas de mauvaise journée"))
    try:
        parse_ranges(getattr(r, "trading_hours", ""))
        parse_days(getattr(r, "trading_days", ""))
    except ValueError:
        out.append(("ERREUR", f"RISK_TRADING_HOURS/DAYS illisible : '{r.trading_hours}' / '{r.trading_days}' "
                              f"(format attendu : 08:00-22:00 et 1-5)"))
    hour = getattr(b, "daily_report_hour", -1)
    if not -1 <= hour <= 23:
        out.append(("ERREUR", f"BOT_DAILY_REPORT_HOUR={hour} : doit être entre 0 et 23 (ou -1)"))
    if getattr(b, "pending_expiry_minutes", 0) <= 0:
        out.append(("INFO", "BOT_PENDING_EXPIRY_MINUTES=0 : un ordre en attente reste actif indéfiniment"))
    if cfg.mt5.allow_real:
        out.append(("ATTENTION", "MT5_ALLOW_REAL=true : le bot peut trader sur un compte RÉEL"))
    return out
