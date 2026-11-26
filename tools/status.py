"""
tools/status.py - "Est-ce que mon bot tourne, et qu'a-t-il d'ouvert ?"

    venv\Scripts\python.exe -m tools.status

Lit state/heartbeat.json (aucun accès à Telegram), puis les positions et ordres du bot
sur MT5 en LECTURE SEULE (aucun ordre envoyé).
"""
from __future__ import annotations

import re
import sys
from datetime import datetime

from utils.heartbeat import read_heartbeat

BOT_MAGIC = 202401


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    hb = read_heartbeat()
    if hb is None:
        print("🔴 Bot : aucun signe de vie (jamais lancé, ou state/heartbeat.json absent)")
    elif hb["alive"]:
        print(f"🟢 Bot en marche (PID {hb.get('pid')}, dernier signe de vie il y a {hb['age_s']:.0f} s) — "
              f"dernier signal #{hb.get('last_signal_msg_id')}, {hb.get('tracked_trades', 0)} trade(s) suivi(s) pour le BE")
    else:
        print(f"🔴 Bot arrêté (dernier signe de vie : {datetime.fromtimestamp(hb['ts']):%d/%m %H:%M:%S})")

    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("MetaTrader5 non installé — positions non affichées")
        return 0
    if not mt5.initialize():
        print(f"MT5 non joignable : {mt5.last_error()}")
        return 0
    try:
        pos = [p for p in (mt5.positions_get() or []) if p.magic == BOT_MAGIC]
        ords = [o for o in (mt5.orders_get() or []) if o.magic == BOT_MAGIC]
        print(f"\nPositions du bot : {len(pos)}")
        for p in pos:
            side = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
            print(f"  #{p.ticket} {side} {p.volume} @ {p.price_open} SL {p.sl} TP {p.tp or 'OPEN'} "
                  f"→ {p.profit:+.2f}  ({p.comment})")
        print(f"Ordres en attente du bot : {len(ords)}")
        for o in ords:
            print(f"  #{o.ticket} @ {o.price_open} SL {o.sl} TP {o.tp or 'OPEN'} ({o.comment})")
        signals = {re.match(r"TG#(\d+)_", x.comment or "").group(1) for x in pos + ords
                   if re.match(r"TG#(\d+)_", x.comment or "")}
        if signals:
            print("Signaux concernés : " + ", ".join(f"#{s}" for s in sorted(signals)))
    finally:
        mt5.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
