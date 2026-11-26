"""
utils/trading_hours.py - Plages horaires où le bot a le droit d'ouvrir des trades.

Format (heure locale du PC) :
  RISK_TRADING_HOURS="08:00-22:00"            une plage
  RISK_TRADING_HOURS="08:00-12:00,14:00-21:30" plusieurs plages
  RISK_TRADING_HOURS="22:00-02:00"            plage qui passe minuit
  RISK_TRADING_DAYS="1-5"                     lundi (1) → vendredi (5) ; "1,2,3" possible
Vide = pas de restriction. Les trades déjà ouverts sont toujours gérés (BE, clôtures...).
"""
from __future__ import annotations

from datetime import datetime, time
from typing import List, Optional, Set, Tuple


def _parse_hhmm(s: str) -> time:
    h, m = s.strip().split(":")
    return time(int(h), int(m))


def parse_ranges(spec: str) -> List[Tuple[time, time]]:
    ranges = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        a, b = part.split("-")
        ranges.append((_parse_hhmm(a), _parse_hhmm(b)))
    return ranges


def parse_days(spec: str) -> Set[int]:
    days: Set[int] = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            days.update(range(a, b + 1))
        else:
            days.add(int(part))
    return days


def is_trading_time(hours: str = "", days: str = "", now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    allowed_days = parse_days(days)
    if allowed_days and now.isoweekday() not in allowed_days:
        return False
    ranges = parse_ranges(hours)
    if not ranges:
        return True
    t = now.time()
    for start, end in ranges:
        if start <= end:
            if start <= t < end:
                return True
        elif t >= start or t < end:          # plage qui passe minuit
            return True
    return False
