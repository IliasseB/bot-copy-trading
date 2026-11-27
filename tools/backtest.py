r"""
tools/backtest.py - Rejoue les signaux du canal sur les VRAIS ticks MT5 (lecture seule).

    venv\Scripts\python.exe -m tools.backtest --export state/channel.json
    venv\Scripts\python.exe -m tools.backtest --export state/channel.json --legs 1 2 --delay 0 30

Pour chaque signal : entrée comme le bot (marché si le prix est dans la zone, sinon ordre au
bord), puis les messages de gestion du canal (réponses) appliqués à leur heure réelle avec le
vrai parser. Résultat en $ pour 0,01 lot par position.
Prérequis : un export (tools.export_channel) et MT5 ouvert (historique de ticks du courtier).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from parser.regex_parser import RegexParser, looks_like_signal
from parser.update_parser import UpdateCommand, parse_update, resolve_price

UTC = dt.timezone.utc
HORIZON_S = 4 * 3600
PENDING_EXPIRY_S = 30 * 60
ZONE_MARGIN = 2.0
BE_OFFSET = 0.10
Ticks = Tuple[np.ndarray, np.ndarray, np.ndarray]     # (secondes depuis le signal, bid, ask)


def first(mask: np.ndarray, start: int = 0) -> Optional[int]:
    idx = np.flatnonzero(mask[start:])
    return start + int(idx[0]) if len(idx) else None


def server_offset_h(t: dt.datetime) -> int:
    """Heure serveur des courtiers MT5 type Pepperstone : UTC+3 pendant l'heure d'été US, sinon UTC+2."""
    y = t.year
    mar = dt.datetime(y, 3, 8, tzinfo=UTC)
    dst_start = mar + dt.timedelta(days=(6 - mar.weekday()) % 7, hours=7)     # 2e dimanche de mars
    nov = dt.datetime(y, 11, 1, tzinfo=UTC)
    dst_end = nov + dt.timedelta(days=(6 - nov.weekday()) % 7, hours=6)       # 1er dimanche de novembre
    return 3 if dst_start <= t < dst_end else 2


# ── Chargement ───────────────────────────────────────────────────────────────

def load_signals(rows: List[dict]) -> List[dict]:
    rp, out = RegexParser(), []
    for r in rows:
        if not looks_like_signal(r.get("text", "")):
            continue
        s, _ = rp.parse(r["text"], r["id"], 1)
        if s is None or s.stop_loss is None or not s.take_profits or s.take_profits[0] is None:
            continue
        out.append(dict(id=r["id"], t=dt.datetime.fromisoformat(r["date"]),
                        dir=str(getattr(s.direction, "value", s.direction)),
                        lo=s.entry_low, hi=s.entry_high, sl=s.stop_loss, tps=list(s.take_profits)))
    return out


def management_events(rows: List[dict], signal_ids: set) -> Dict[int, list]:
    byid = {r["id"]: r for r in rows}
    ev: Dict[int, list] = collections.defaultdict(list)
    for r in rows:
        if r["id"] in signal_ids or not r.get("reply_to"):
            continue
        cur, root = r, None
        for _ in range(10):                       # remonte le fil de réponses
            if not cur or not cur.get("reply_to"):
                break
            if cur["reply_to"] in signal_ids:
                root = cur["reply_to"]
                break
            cur = byid.get(cur["reply_to"])
        if root:
            cmds = parse_update(r.get("text", ""))
            if cmds:
                ev[root].append((dt.datetime.fromisoformat(r["date"]), cmds))
    return ev


def fetch_ticks(mt5, sig: dict, symbol: str = "XAUUSD") -> Optional[Ticks]:
    off = server_offset_h(sig["t"])
    start = sig["t"].replace(tzinfo=None) + dt.timedelta(hours=off) - dt.timedelta(seconds=5)
    tk = mt5.copy_ticks_range(symbol, start.replace(tzinfo=UTC),
                              (start + dt.timedelta(seconds=HORIZON_S + 300)).replace(tzinfo=UTC), mt5.COPY_TICKS_INFO)
    if tk is None or len(tk) < 100:
        return None
    t0 = (sig["t"].replace(tzinfo=None) + dt.timedelta(hours=off)).replace(tzinfo=UTC).timestamp()
    return tk["time_msc"] / 1000.0 - t0, tk["bid"].astype(float), tk["ask"].astype(float)


# ── Simulation ───────────────────────────────────────────────────────────────

def _entry(sig, ticks, evs, tps, delay):
    t, bid, ask = ticks
    buy = sig["dir"] == "BUY"
    px_in, px_out = (ask, bid) if buy else (bid, ask)
    i0 = first(t >= delay + 1.0)
    if i0 is None:
        return None
    p = px_in[i0]
    lo, hi = (sig["lo"], sig["hi"]) if sig.get("lo") else (p, p)
    if min(abs(lo - p), abs(hi - p)) / p * 100 > 1.0 or (buy and p <= sig["sl"]) or (not buy and p >= sig["sl"]):
        return None
    if (lo - ZONE_MARGIN) <= p <= (hi + ZONE_MARGIN):
        return i0, p
    e_price = lo if buy else hi
    end = first(t >= delay + 1 + PENDING_EXPIRY_S) or len(t)
    stop = next((et for et, cm in evs if any(c.kind in ("CLOSE", "CANCEL", "SL_HIT", "BE_HIT", "TP_HIT") for c in cm)), None)
    if stop is not None:
        end = min(end, first(t >= stop) or len(t))
    seg = px_in[i0:end]
    if buy:
        hit = (seg <= e_price) if e_price < p else (seg >= e_price)
    else:
        hit = (seg >= e_price) if e_price > p else (seg <= e_price)
    k = first(hit)
    tp_first = first((px_out[i0:end] >= tps[0]) if buy else (px_out[i0:end] <= tps[0])) if tps[0] else None
    if k is None or (tp_first is not None and tp_first < k):
        return None
    return i0 + k, e_price


def simulate(sig: dict, ticks: Ticks, events=(), n_legs: int = 2, delay: float = 0.0) -> Optional[dict]:
    """Rejoue un signal ; renvoie {"pnl", "legs"} en $ pour 0,01 lot par position, ou None si pas d'entrée."""
    t, bid, ask = ticks
    buy = sig["dir"] == "BUY"
    sgn = 1 if buy else -1
    px_out = bid if buy else ask
    evs = sorted(((e[0] - sig["t"]).total_seconds(), e[1]) for e in events)
    tps = (sig["tps"] or [None])[:n_legs]
    entry = _entry(sig, ticks, evs, tps, delay)
    if entry is None:
        return None
    fi, fp = entry
    legs = [dict(n=i + 1, tp=tps[i], sl=sig["sl"], open=True, pnl=0.0) for i in range(len(tps))]
    end_i = first(t >= t[fi] + HORIZON_S) or len(t) - 1
    evs = [(et, cm) for et, cm in evs if t[fi] < et <= t[end_i]]
    st = {"cur": fi, "be": False}

    def close(leg, price):
        leg["open"], leg["pnl"] = False, sgn * (price - fp)

    def set_be():
        st["be"] = True
        for o in legs:
            if o["open"]:
                o["sl"] = fp + sgn * BE_OFFSET

    def run_until(stop_i):
        while st["cur"] < stop_i and any(leg["open"] for leg in legs):
            seg = px_out[st["cur"]:stop_i]
            best = None
            for leg in legs:
                if not leg["open"]:
                    continue
                s = first((seg <= leg["sl"]) if buy else (seg >= leg["sl"]))
                if s is not None and (best is None or s < best[0]):
                    best = (s, leg, "SL")
                if leg["tp"]:
                    k = first((seg >= leg["tp"]) if buy else (seg <= leg["tp"]))
                    if k is not None and (best is None or k < best[0]):
                        best = (k, leg, "TP")
            if best is None:
                st["cur"] = stop_i
                return
            k, leg, why = best
            level = leg["sl"] if why == "SL" else leg["tp"]
            close(leg, level)
            st["cur"] += k
            if why == "TP" and leg["n"] == 1 and not st["be"]:
                set_be()                                    # boucle BE du bot
            if why == "SL":
                for o in legs:
                    if o["open"] and o["sl"] == level:
                        close(o, level)

    for et, cmds in evs:
        run_until(min(first(t >= et) or end_i, end_i))
        if not any(leg["open"] for leg in legs):
            break
        price = px_out[min(st["cur"], len(t) - 1)]
        for c in cmds:
            _apply(c, legs, price, fp, buy, sgn, close, set_be, st)
    run_until(end_i)
    for leg in legs:
        if leg["open"]:
            close(leg, px_out[end_i])
    return {"pnl": sum(leg["pnl"] for leg in legs), "legs": [leg["pnl"] for leg in legs]}


def _apply(c: UpdateCommand, legs, price, fp, buy, sgn, close, set_be, st) -> None:
    """Applique une commande du canal comme le fait TradeExecutor.apply_update."""
    alive = [leg for leg in legs if leg["open"]]
    if not alive:
        return
    in_profit = sgn * (price - fp) > 0
    first_tp_leg = next((x for x in alive if x["tp"]), alive[0])
    sel = alive if not c.legs else [leg for leg in alive if leg["n"] in c.legs or (0 in c.legs and leg is first_tp_leg)]
    if c.kind == "CLOSE":
        for leg in alive:
            if not c.only_profit or in_profit:
                close(leg, price)
    elif c.kind in ("SL_HIT", "BE_HIT"):
        for leg in sel:
            close(leg, price)
    elif c.kind in ("CLOSE_LEG", "TP_HIT"):
        for leg in (sel if c.legs else [first_tp_leg]):
            if (c.kind == "CLOSE_LEG" and not c.only_profit) or in_profit:
                close(leg, price)
        if not st["be"] and not legs[0]["open"]:
            set_be()
    elif c.kind == "PARTIAL":
        k = int(len(sel) * (c.percent or 50) / 100 + 1e-9)
        for leg in sorted(sel, key=lambda x: x["n"])[:k]:
            close(leg, price)
    elif c.kind == "BE":
        be = fp + sgn * BE_OFFSET
        for leg in sel:
            if (buy and leg["sl"] < be < price) or (not buy and leg["sl"] > be > price):
                leg["sl"] = be
    elif c.kind in ("SL", "TP"):
        v = resolve_price(c.value, fp)
        if abs(v - fp) / fp * 100 > 3:
            return
        if c.kind == "SL" and ((buy and v < price) or (not buy and v > price)):
            for leg in alive:
                protected = leg["sl"] >= fp if buy else leg["sl"] <= fp
                worse = v < leg["sl"] if buy else v > leg["sl"]
                if not (protected and worse):
                    leg["sl"] = v
        elif c.kind == "TP" and ((buy and v > price) or (not buy and v < price)):
            targets = [leg for leg in alive if leg["n"] == c.tp_index] if c.tp_index else \
                [leg for leg in sorted(alive, key=lambda x: x["n"]) if leg["tp"]][:1]
            for leg in targets:
                leg["tp"] = v


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Backtest des signaux du canal sur les ticks MT5")
    ap.add_argument("--export", type=Path, default=Path("state") / "channel.json")
    ap.add_argument("--legs", type=int, nargs="+", default=[1, 2])
    ap.add_argument("--delay", type=float, nargs="+", default=[0])
    ap.add_argument("--symbol", default="XAUUSD")
    a = ap.parse_args()

    import logging
    logging.disable(logging.CRITICAL)
    rows = json.loads(a.export.read_text(encoding="utf-8"))
    sigs = load_signals(rows)
    events = management_events(rows, {s["id"] for s in sigs})

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 non joignable : {mt5.last_error()}")
        return 1
    try:
        data = [(s, tk) for s in sigs if (tk := fetch_ticks(mt5, s, a.symbol)) is not None]
    finally:
        mt5.shutdown()
    if not data:
        print("Aucun tick MT5 pour ces signaux.")
        return 1
    print(f"{len(sigs)} signaux, {len(data)} avec ticks MT5 ({data[0][0]['t']:%d/%m/%Y} → {data[-1][0]['t']:%d/%m/%Y})")
    for n_legs in a.legs:
        for delay in a.delay:
            res = [r for r in (simulate(s, tk, events.get(s["id"], []), n_legs, delay) for s, tk in data) if r]
            pnl = [r["pnl"] for r in res]
            eq = np.cumsum(pnl) if pnl else np.array([0.0])
            dd = float(np.max(np.maximum.accumulate(eq) - eq))
            win = np.mean([x > 0 for x in pnl]) * 100 if pnl else 0
            print(f"{n_legs} position(s), délai {delay:g} s : {len(res)} trades, {win:.0f}% gagnants, "
                  f"résultat {sum(pnl):+.1f} $, pire creux {dd:.1f} $")
    return 0


if __name__ == "__main__":
    sys.exit(main())
