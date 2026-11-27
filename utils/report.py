"""
utils/report.py - Récapitulatif de la journée (envoyé le soir sur Telegram).
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import date
from typing import Dict, List, Optional


def format_daily_report(deals: List[dict], day: Optional[date] = None, currency: str = "€") -> str:
    day = day or date.today()
    title = f"📊 Récap du {day.strftime('%d/%m/%Y')}"
    if not deals:
        return f"{title}\nAucun trade clôturé aujourd'hui."
    by_signal: "OrderedDict[object, Dict]" = OrderedDict()
    for d in deals:
        s = by_signal.setdefault(d.get("signal"), {"profit": 0.0, "legs": 0, "reasons": []})
        s["profit"] += d.get("profit", 0.0)
        s["legs"] += 1
        s["reasons"].append(d.get("reason", "?"))
    total = sum(s["profit"] for s in by_signal.values())
    wins = sum(1 for s in by_signal.values() if s["profit"] > 0)
    losses = sum(1 for s in by_signal.values() if s["profit"] < 0)
    lines = [title,
             f"Signaux : {len(by_signal)} — ✅ {wins} gagnant(s) — ❌ {losses} perdant(s)",
             f"Résultat : {total:+.2f} {currency}", ""]
    for sig, s in by_signal.items():
        icon = "✅" if s["profit"] > 0 else ("❌" if s["profit"] < 0 else "🟰")
        lines.append(f"{icon} #{sig} : {s['profit']:+.2f} {currency} ({' + '.join(s['reasons'])})")
    return "\n".join(lines)
