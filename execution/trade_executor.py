"""
execution/trade_executor.py
Version Augmentée : Support des TP multiples + Gestion des TP "Open" (running) + Break-Even.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from parser.signal_model import SignalStatus, TradingSignal
from parser.update_parser import UpdateCommand, resolve_price
from risk_management.risk_manager import RiskManager, floor_to_step
from utils.logger import get_logger

from .mt5_bridge import MT5Bridge, MT5ConnectionError, MT5ExecutionError

log = get_logger("executor")


class TradeExecutor:

    def __init__(self, mt5: MT5Bridge, risk: RiskManager, db_writer=None,
                 confidence_threshold: float = 0.45, be_offset_points: int = 0,
                 max_entry_distance_percent: float = 1.0,
                 partial_close_enabled: bool = True,
                 max_update_distance_percent: float = 3.0):
        # Clôtures partielles ("SÉCURISEZ 50%") appliquées sur MT5 ?
        self._partial_enabled = partial_close_enabled
        # Garde-fou : un SL/TP envoyé par message à plus de X % de l'entrée = faute de frappe → ignoré
        self._max_update_dist_pct = max_update_distance_percent
        self._mt5 = mt5
        self._risk = risk
        self._db = db_writer
        self._threshold = confidence_threshold
        # Décalage du BE en points (or : 1 point = 0.01$). Ex: 10 = BE à entrée + 0.10$
        # pour couvrir la commission Razor. 0 = BE pile au prix d'entrée.
        self._be_offset_points = max(0, int(be_offset_points))
        self._max_entry_dist_pct = max_entry_distance_percent
        # Registre des trades actifs pour le suivi du Break-Even
        self._tracked_trades: List[Dict[str, Any]] = []

    async def execute(self, signal: TradingSignal) -> TradingSignal:
        t_start = time.time_ns()

        # ── Confidence Check ──────────────────────────────────────────────
        if signal.confidence < self._threshold:
            return self._reject(signal, f"Confiance trop faible: {signal.confidence:.2f}")

        # ── Snapshot marché en 1 seul aller-retour MT5 (balance + spread + prix) ──
        try:
            snapshot = await self._mt5.get_market_snapshot(signal.symbol, signal.direction)
            balance, spread, current_price = snapshot["balance"], snapshot["spread"], snapshot["price"]
        except MT5ConnectionError as e:
            return self._reject(signal, f"MT5 déconnecté: {e}")
        except MT5ExecutionError as e:
            return self._reject(signal, f"Impossible d'obtenir le prix: {e}")
        except Exception as e:
            log.error(f"Erreur inattendue MT5 (snapshot marché): {e}")
            return self._reject(signal, f"Erreur MT5 inattendue: {e}")

        # ── Garde-fou faute de frappe : entrée beaucoup trop loin du prix ──
        # (ex: le trader écrit 4166 au lieu de 4066 puis corrige 2 min après)
        far = self._entry_too_far(signal, current_price)
        if far:
            return self._reject(signal, far)

        # ── Risk check ────────────────────────────────────────────────────
        risk_result = await self._risk.check(signal, balance, spread, current_price)
        if not risk_result.approved:
            return self._reject(signal, f"Risk refusé: {risk_result.reason}")

        total_lot = risk_result.lot_size

        in_zone = signal.is_price_in_zone(current_price)

        # ── TPs : None = jambe "OPEN" (sans prix cible, reste ouverte) ─────
        raw_tps = signal.take_profits if signal.take_profits else [signal.primary_tp]
        tps_cleaned: List[float] = [
            float(tp) if tp is not None else 0.0  # 0.0 sur MT5 = pas de TP
            for tp in raw_tps
        ]
        if not tps_cleaned:
            tps_cleaned = [0.0]

        num_tps = len(tps_cleaned)

        # Récupération des contraintes de lot du broker (Pepperstone)
        try:
            info = await self._mt5.get_symbol_trade_info(signal.symbol)
            vol_min = info.get("volume_min", 0.01)
            vol_step = info.get("volume_step", 0.01)
        except Exception:
            info = {}
            vol_min = 0.01
            vol_step = 0.01

        # Calcul du lot par sous-position : autant de positions que le lot le permet
        # (0.02 lot avec 3 TP → 2 positions : TP1 + TP2 ; 0.03 → TP1 + TP2 + TP3)
        n_legs = max(1, min(num_tps, int(floor_to_step(total_lot / vol_min, 1) if vol_min else num_tps)))
        part_lot = round(floor_to_step(total_lot / n_legs, vol_step), 2)
        if part_lot < vol_min:
            n_legs, part_lot = 1, total_lot
        lots_to_send = [part_lot] * n_legs
        lots_to_send[-1] = round(total_lot - part_lot * (n_legs - 1), 2)
        lots_to_send += [0.0] * (num_tps - n_legs)

        if n_legs < num_tps:
            skipped = [f"TP{i+1}" for i in range(n_legs, num_tps)]
            log.warning(
                f"⚠️ Lot total {total_lot} : assez pour {n_legs} position(s) sur {num_tps} TP "
                f"→ {', '.join(skipped)} non pris. Il faut {round(vol_min * num_tps, 2)} lot pour tout prendre "
                f"(ou RISK_LOT_PER_TP=0.01)."
            )

        tickets: List[int] = []
        filled_prices: List[float] = []

        # Structure de tracking pour la boucle Break-Even
        trade_track = {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "entry_price": 0.0,
            "tp1": tps_cleaned[0],  # Doit être un prix chiffré pour déclencher le BE
            "tp1_ticket": None,
            "other_tickets": [],
            "be_triggered": False,
            "be_done": set(),        # tickets déjà passés en BE
            "tick_size": info.get("tick_size", 0.01) if isinstance(info, dict) else 0.01,
        }

        # ── Envoi des ordres sur MT5 ──────────────────────────────────────
        for i, (p_lot, tp_val) in enumerate(zip(lots_to_send, tps_cleaned, strict=True)):
            if p_lot <= 0:
                continue

            # Le n° de jambe est TOUJOURS dans le commentaire ("_TP2_OPEN") : le canal dit
            # "METTEZ BE SUR TP2" même quand le TP2 est "OPEN"
            comment_tp = f"TP{i+1}_OPEN" if tp_val == 0.0 else f"TP{i+1}"
            trade_comment = f"TG#{signal.telegram_msg_id}_{comment_tp}"

            try:
                if in_zone:
                    log.info(f"💥 EXECUTION MARKET (Partie {i+1}) — lot={p_lot} tp={tp_val if tp_val > 0 else 'OPEN'}")
                    res = await self._mt5.send_market_order(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        lot=p_lot,
                        sl=signal.stop_loss,
                        tp=tp_val, # Envoie 0.0 si OPEN
                        comment=trade_comment,
                        known_price=current_price if i == 0 else None,
                    )
                else:
                    entry = signal.entry or current_price
                    log.info(f"⏳ EXECUTION PENDING (Partie {i+1}) — lot={p_lot} entry={entry} "
                             f"tp={tp_val if tp_val > 0 else 'OPEN'}")
                    res = await self._mt5.send_pending_order(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        order_type=signal.order_type,
                        lot=p_lot,
                        entry=entry,
                        sl=signal.stop_loss,
                        tp=tp_val,
                        comment=trade_comment,
                    )

                tickets.append(res["ticket"])
                filled_prices.append(res["filled_price"])

                if i == 0:
                    trade_track["tp1_ticket"] = res["ticket"]
                    trade_track["entry_price"] = res["filled_price"]
                else:
                    trade_track["other_tickets"].append(res["ticket"])

                if i == 0:
                    self._risk.on_trade_opened()   # 1 signal = 1 trade, même avec 3 TP

            except MT5ExecutionError as e:
                log.error(f"❌ Échec de la fraction d'ordre {comment_tp}: {e}")
                if i == 0:
                    return self._mark_failed(signal, str(e))

        # On active le suivi BE s'il y a un TP1 chiffré et d'autres tickets à protéger (qu'ils soient chiffrés ou OPEN)
        if trade_track["tp1_ticket"] is not None and trade_track["other_tickets"] and trade_track["tp1"] > 0:
            self._tracked_trades.append(trade_track)
            log.info(
                f"🛡️ BE armé — TP1 ticket={trade_track['tp1_ticket']} → "
                f"BE sur {trade_track['other_tickets']} dès que TP1 est touché"
            )

        # ── Finalisation du Signal ────────────────────────────────────────
        filled = filled_prices[0] if filled_prices else current_price
        exec_ms = (time.time_ns() - t_start) / 1_000_000

        ref = self._slippage_reference(signal, filled)
        if ref is not None:
            self._risk.check_slippage(ref, filled, signal.symbol)

        signal = signal.model_copy(update={
            "status":        SignalStatus.EXECUTED,
            "ticket":        tickets[0] if tickets else None,
            "filled_price":  filled,
            "lot_size":      total_lot,
            "executed_at_ns": time.time_ns(),
        })

        log.info(f"🎯 PIPELINE MULTI-TP REUSSI — Tickets={tickets} | Temps={exec_ms:.0f}ms")
        return signal

    @staticmethod
    def _slippage_reference(signal: TradingSignal, filled: float) -> Optional[float]:
        """Prix de référence du glissement : rempli DANS la zone = pas de glissement ;
        sinon on mesure par rapport au bord de zone le plus proche."""
        lo = signal.entry_low or signal.entry
        hi = signal.entry_high or signal.entry
        if lo is None or hi is None or not filled:
            return None
        lo, hi = min(lo, hi), max(lo, hi)
        if lo <= filled <= hi:
            return None
        return lo if filled < lo else hi

    # ── BREAK-EVEN ────────────────────────────────────────────────────────────

    def _be_price(self, direction: str, price_open: float, tick_size: float) -> float:
        offset = self._be_offset_points * tick_size
        be = price_open + offset if direction == "BUY" else price_open - offset
        return round(be, 5)

    @staticmethod
    def _already_protected(direction: str, current_sl: float, be: float) -> bool:
        if not current_sl:
            return False
        return current_sl >= be if direction == "BUY" else current_sl <= be

    async def monitor_and_adjust_be(self) -> None:
        """
        Appelée chaque seconde. Pour chaque signal multi-TP :
          1. TP1 encore en attente (pending) ou ouvert → on attend.
          2. TP1 disparu → on demande à MT5 COMMENT il s'est fermé :
               - "SL"  : les autres jambes ont le même SL, elles sortent aussi → rien à faire
               - None  : ordre pending annulé sans jamais être rempli → pas de BE
               - "TP" ou autre (fermeture manuelle...) → TP2/TP3/OPEN passent en BE
          3. Chaque jambe passe en BE au prix d'entrée RÉEL de sa position (+ offset).
             Une jambe encore en pending passera en BE dès qu'elle sera remplie.
             Une modification ratée est retentée à la boucle suivante.
        """
        if not self._tracked_trades:
            return

        try:
            state = await self._mt5.get_active_state()
        except Exception as e:
            log.warning(f"Impossible de lire positions/ordres — cycle BE ignoré: {e}")
            return

        positions, orders = state["positions"], state["orders"]
        still_active: List[Dict[str, Any]] = []

        for trade in self._tracked_trades:
            others_alive = [t for t in trade["other_tickets"] if t in positions or t in orders]
            if not others_alive:
                continue  # plus rien à protéger → on arrête de suivre ce signal

            tp1 = trade["tp1_ticket"]

            if not trade["be_triggered"]:
                if tp1 is not None and (tp1 in positions or tp1 in orders):
                    still_active.append(trade)
                    continue

                reason = await self._mt5.get_close_reason(tp1) if tp1 is not None else "OTHER"
                if reason == "SL":
                    log.info(f"TP1 ticket={tp1} fermé par le SL — pas de BE (les autres jambes sortent aussi)")
                    still_active.append(trade)  # on garde au cas où une jambe resterait ouverte
                    trade["be_triggered"] = True
                    trade["be_disabled"] = True
                    continue
                if reason is None:
                    log.info(f"TP1 ticket={tp1} jamais rempli (pending annulé) — pas de BE")
                    continue

                trade["be_triggered"] = True
                log.info(
                    f"📢 [BE TRIGGER] TP1 touché (ticket={tp1}, fermeture={reason}) sur {trade['symbol']} "
                    f"→ passage en BE de {others_alive}"
                )

            if trade.get("be_disabled"):
                if others_alive:
                    still_active.append(trade)
                continue

            # Appliquer le BE à chaque jambe ouverte pas encore protégée
            for ticket in others_alive:
                if ticket in trade["be_done"] or ticket not in positions:
                    continue  # déjà fait, ou encore en pending (on réessaiera)
                pos = positions[ticket]
                be = self._be_price(pos["type"], pos["price_open"], trade["tick_size"])

                if self._already_protected(pos["type"], pos["sl"], be):
                    trade["be_done"].add(ticket)
                    continue
                try:
                    ok = await self._mt5.modify_position_sltp(ticket, sl=be)
                except Exception as e:
                    ok = False
                    log.error(f"❌ [BE ERROR] ticket #{ticket}: {e}")
                if ok:
                    trade["be_done"].add(ticket)
                    log.info(f"✅ [BE OK] ticket #{ticket} SL → {be} (entrée {pos['price_open']})")
                else:
                    log.warning(f"⏳ [BE RETRY] ticket #{ticket} — nouvel essai dans 1s")

            still_active.append(trade)

        self._tracked_trades = still_active

    async def recover_tracked_trades(self) -> None:
        """
        Au démarrage : reconstruit le suivi BE à partir des positions/ordres du bot
        déjà présents sur MT5 (commentaires "TG#<msg_id>_TP1", "_TP2", "_OPEN"...).
        Sans ça, un redémarrage du bot faisait perdre le BE des trades en cours.
        """
        import re
        try:
            state = await self._mt5.get_active_state()
        except Exception as e:
            log.warning(f"Récupération BE impossible: {e}")
            return

        groups: Dict[str, Dict[str, Any]] = {}
        pattern = re.compile(r"^TG#(\d+)_(TP\d+|OPEN)")
        for kind in ("positions", "orders"):
            for ticket, d in state[kind].items():
                if d.get("magic") != MT5Bridge.BOT_MAGIC:
                    continue
                m = pattern.match(d.get("comment") or "")
                if not m:
                    continue
                g = groups.setdefault(m.group(1), {"tp1": None, "others": [], "symbol": d["symbol"]})
                if m.group(2) == "TP1":
                    g["tp1"] = ticket
                else:
                    g["others"].append(ticket)

        info = {}
        try:
            info = await self._mt5.get_symbol_trade_info("XAUUSD")
        except Exception:
            pass

        for msg_id, g in groups.items():
            if not g["others"]:
                continue
            self._tracked_trades.append({
                "symbol": g["symbol"], "direction": None, "entry_price": 0.0, "tp1": 1.0,
                "tp1_ticket": g["tp1"], "other_tickets": g["others"],
                "be_triggered": False, "be_done": set(),
                "tick_size": info.get("tick_size", 0.01) if info else 0.01,
            })
            etat = "TP1 encore actif" if g["tp1"] else "TP1 déjà fermé → BE appliqué maintenant"
            log.info(f"♻️ Suivi BE récupéré — signal #{msg_id} : {etat}, jambes={g['others']}")

    def _entry_too_far(self, signal: TradingSignal, price: float) -> Optional[str]:
        if not price or self._max_entry_dist_pct <= 0:
            return None
        levels = [x for x in (signal.entry_low, signal.entry_high, signal.entry) if x]
        if not levels:
            return None
        dist = min(abs(x - price) for x in levels) / price * 100
        if dist > self._max_entry_dist_pct:
            return (f"Entrée {levels[0]} à {dist:.1f}% du prix actuel {price} "
                    f"(> {self._max_entry_dist_pct}%) — faute de frappe probable, "
                    f"on attend une correction du message")
        return None

    # ── TRADES D'UN SIGNAL (retrouvés sur MT5 par leur commentaire) ───────────

    @staticmethod
    def _leg_index(comment: str) -> Optional[int]:
        """'TG#10511_TP2' → 1 (index 0-based) ; 'TG#10511_OPEN' → None."""
        m = re.search(r"_TP(\d+)", comment or "")
        return int(m.group(1)) - 1 if m else None

    async def _legs_for(self, msg_id: int) -> Tuple[Dict[int, dict], Dict[int, dict]]:
        state = await self._mt5.get_active_state()
        prefix = f"TG#{msg_id}_"
        pos = {t: d for t, d in state["positions"].items() if (d.get("comment") or "").startswith(prefix)}
        ords = {t: d for t, d in state["orders"].items() if (d.get("comment") or "").startswith(prefix)}
        return pos, ords

    def _is_be_done(self, ticket: int) -> bool:
        return any(ticket in t.get("be_done", set()) for t in self._tracked_trades)

    def _forget_tracking(self, msg_tickets: set) -> None:
        self._tracked_trades = [
            t for t in self._tracked_trades
            if t["tp1_ticket"] not in msg_tickets and not set(t["other_tickets"]) & msg_tickets
        ]

    async def has_trades_for(self, msg_id: int) -> bool:
        try:
            pos, ords = await self._legs_for(msg_id)
        except Exception:
            return False
        return bool(pos or ords)

    # ── SIGNAL MODIFIÉ (le trader édite son message) ──────────────────────────

    async def sync_edited_signal(self, signal: TradingSignal) -> Optional[TradingSignal]:
        """
        Le message du signal a été modifié alors que des ordres existent déjà.
          • Ordres encore EN ATTENTE → on les annule et on replace le signal corrigé
            (s'il est maintenant dans la zone, ça part directement au marché).
          • Positions DÉJÀ OUVERTES  → on met à jour SL et TP de chaque jambe
            (sauf le SL d'une jambe déjà passée en BE : on ne la remet pas en risque).
        Retourne le signal ré-exécuté, ou None si seules des mises à jour ont été faites.
        """
        msg_id = signal.telegram_msg_id
        pos, ords = await self._legs_for(msg_id)
        if not pos and not ords:
            return None

        direction = str(getattr(signal.direction, "value", signal.direction))
        old_dir = next(iter({**pos, **ords}.values()))["type"]
        if direction != old_dir:
            log.warning(f"⚠️ [EDIT #{msg_id}] Direction changée {old_dir} → {direction} — "
                        f"annulation des ordres en attente, positions ouvertes laissées telles quelles")
            for t in ords:
                await self._mt5.cancel_order(t)
            return await self.execute(signal) if not pos else None

        # 1) Ordres en attente → annuler + replacer proprement (seulement si ça a changé :
        #    beaucoup de canaux éditent le signal juste pour ajouter "TP1 ✅")
        if ords and not pos and self._orders_match(signal, ords):
            log.info(f"[EDIT #{msg_id}] Entrée/SL/TP inchangés — ordres en attente conservés")
            return None
        if ords and not pos:
            log.info(f"✏️ [EDIT #{msg_id}] Signal corrigé — annulation de {list(ords)} et nouveau placement")
            for t in ords:
                await self._mt5.cancel_order(t)
            self._forget_tracking(set(ords))
            return await self.execute(signal)

        if ords:  # mélange (rare) : on annule ce qui n'est pas encore déclenché
            for t in ords:
                await self._mt5.cancel_order(t)

        # 2) Positions ouvertes → mise à jour SL / TP
        tps = [tp for tp in (signal.take_profits or [])]
        for ticket, d in pos.items():
            idx = self._leg_index(d["comment"])
            new_tp = tps[idx] if idx is not None and idx < len(tps) and tps[idx] else None
            new_sl = signal.stop_loss
            if self._is_be_done(ticket):
                new_sl = None   # déjà protégé au BE : on ne recule pas
            if (new_sl is None or abs(new_sl - d["sl"]) < 1e-6) and (new_tp is None or abs(new_tp - d["tp"]) < 1e-6):
                continue
            ok = await self._mt5.modify_position_sltp(ticket, sl=new_sl, tp=new_tp)
            log.info(f"{'✅' if ok else '❌'} [EDIT #{msg_id}] ticket #{ticket} sl={new_sl or d['sl']} tp={new_tp or d['tp']}")
        return None

    def _orders_match(self, signal: TradingSignal, ords: Dict[int, dict]) -> bool:
        lo = min(x for x in (signal.entry_low, signal.entry_high, signal.entry) if x) if any(
            (signal.entry_low, signal.entry_high, signal.entry)) else None
        hi = max(x for x in (signal.entry_low, signal.entry_high, signal.entry) if x) if lo else None
        tps = signal.take_profits or []
        for d in ords.values():
            if signal.stop_loss and abs(d["sl"] - signal.stop_loss) > 1e-6:
                return False
            if lo is not None and not (lo - 2.0 <= d["price_open"] <= hi + 2.0):
                return False
            idx = self._leg_index(d["comment"])
            if idx is not None and idx < len(tps) and tps[idx] and abs(d["tp"] - tps[idx]) > 1e-6:
                return False
        return True

    # ── MESSAGE DE GESTION (réponse au signal) ────────────────────────────────

    @staticmethod
    def _leg_no(comment: str) -> int:
        """N° de jambe 1-based : 'TG#1_TP2' → 2 ; 'TG#1_TP2_OPEN' → 2 ; ancien 'TG#1_OPEN' → 99 (dernière)."""
        m = re.search(r"_TP(\d+)", comment or "")
        return int(m.group(1)) if m else 99

    @staticmethod
    def _in_profit(d: dict) -> bool:
        if "profit" in d and d["profit"] is not None:
            return d["profit"] > 0
        cur = d.get("price_current")
        if not cur:
            return False
        return cur > d["price_open"] if d["type"] == "BUY" else cur < d["price_open"]

    def _select_legs(self, pos: Dict[int, dict], legs: List[int]) -> Dict[int, dict]:
        """Jambes visées : [] = toutes ; [0] = 1re jambe avec un TP chiffré encore ouverte ; [2] = TP2..."""
        if not legs:
            return dict(pos)
        out: Dict[int, dict] = {}
        ordered = sorted(pos.items(), key=lambda kv: self._leg_no(kv[1]["comment"]))
        for n in legs:
            if n == 0:
                first = next(((t, d) for t, d in ordered if d.get("tp")), None) or (ordered[0] if ordered else None)
                if first:
                    out[first[0]] = first[1]
            else:
                out.update({t: d for t, d in ordered if self._leg_no(d["comment"]) == n})
        return out

    def _sane(self, price: float, ref: float, what: str, msg_id: int) -> bool:
        if ref and self._max_update_dist_pct > 0 and abs(price - ref) / ref * 100 > self._max_update_dist_pct:
            log.warning(f"[UPDATE #{msg_id}] {what} {price} à plus de {self._max_update_dist_pct}% de l'entrée "
                        f"{ref} — faute de frappe probable, ignoré")
            return False
        return True

    async def _close_many(self, msg_id: int, tickets: Dict[int, dict], label: str, only_profit: bool = False) -> int:
        done = 0
        for t, d in tickets.items():
            if only_profit and not self._in_profit(d):
                log.info(f"[UPDATE #{msg_id}] {label} : ticket #{t} pas en profit — laissé ouvert")
                continue
            ok = await self._mt5.close_position(t)
            done += bool(ok)
            log.info(f"{'✅' if ok else '❌'} [UPDATE #{msg_id}] {label} — ticket #{t} fermé "
                     f"(jambe TP{self._leg_no(d['comment'])}, profit={d.get('profit')})")
        return done

    async def _cancel_pending(self, msg_id: int, ords: Dict[int, dict], why: str) -> int:
        done = 0
        for t in ords:
            done += bool(await self._mt5.cancel_order(t))
        if ords:
            log.info(f"🗑️ [UPDATE #{msg_id}] {len(ords)} ordre(s) en attente annulé(s) — {why}")
        return done

    async def _partial_close(self, msg_id: int, pos: Dict[int, dict], percent: float) -> int:
        """Ferme `percent` % du volume ouvert : jambes entières d'abord (TP1 en premier), puis partiel."""
        if not pos:
            return 0
        if not self._partial_enabled:
            log.info(f"[UPDATE #{msg_id}] Clôture partielle {percent:g}% demandée — désactivée (BOT_UPDATE_PARTIAL_CLOSE=false)")
            return 0
        try:
            info = await self._mt5.get_symbol_trade_info(next(iter(pos.values()))["symbol"])
        except Exception:
            info = {}
        vol_min, step = info.get("volume_min", 0.01), info.get("volume_step", 0.01)
        total = sum(d.get("volume", 0.0) for d in pos.values())
        remaining = round(total * percent / 100, 8)
        done = 0
        for t, d in sorted(pos.items(), key=lambda kv: self._leg_no(kv[1]["comment"])):
            vol = d.get("volume", 0.0)
            if remaining < vol_min - 1e-9:
                break
            if vol <= remaining + 1e-9:
                ok = await self._mt5.close_position(t)
                remaining -= vol
            else:
                part = round(floor_to_step(remaining, step), 2)
                if part < vol_min or vol - part < vol_min - 1e-9:
                    break
                ok = await self._mt5.close_position(t, volume=part)
                remaining -= part
            done += bool(ok)
            log.info(f"{'✅' if ok else '❌'} [UPDATE #{msg_id}] Clôture partielle — ticket #{t} "
                     f"(jambe TP{self._leg_no(d['comment'])})")
        if not done:
            log.info(f"[UPDATE #{msg_id}] Clôture partielle {percent:g}% : volume trop petit ({total} lot, "
                     f"minimum {vol_min}) — rien fermé, le BE protège le reste")
        return done

    async def apply_update(self, msg_id: int, cmds: List[UpdateCommand]) -> int:
        """
        Applique les instructions d'un message de gestion aux trades du signal `msg_id`.
        Retourne le nombre de modifications réussies.
        """
        pos, ords = await self._legs_for(msg_id)
        if not pos and not ords:
            log.info(f"[UPDATE #{msg_id}] Aucun trade du bot pour ce signal — rien à faire")
            return 0

        done = 0
        for cmd in cmds:
            if cmd.kind != "CLOSE":
                pos, ords = await self._legs_for(msg_id)   # état à jour après chaque action
            if not pos and not ords:
                break

            if cmd.kind == "CLOSE":
                done += await self._close_many(msg_id, pos, "Fermeture du trade", cmd.only_profit)
                done += await self._cancel_pending(msg_id, ords, "fermeture du trade")
                self._forget_tracking(set(pos) | set(ords))
                suffix = " (positions en profit seulement)" if cmd.only_profit else ""
                log.info(f"🚪 [UPDATE #{msg_id}] Fermeture demandée{suffix}")
                if not cmd.only_profit:
                    return done
                continue

            if cmd.kind in ("SL_HIT", "BE_HIT"):
                # Le trade du canal est terminé (SL ou BE touché) : on ne garde rien d'ouvert
                targets = self._select_legs(pos, cmd.legs)
                done += await self._close_many(msg_id, targets, f"{cmd.kind.replace('_', ' ')} sur le canal")
                done += await self._cancel_pending(msg_id, ords, "trade terminé sur le canal")
                continue

            if cmd.kind == "CANCEL":
                done += await self._cancel_pending(msg_id, ords, "signal annulé")
                if pos:
                    log.info(f"[UPDATE #{msg_id}] {len(pos)} position(s) déjà ouverte(s) laissée(s)")
                continue

            if cmd.kind == "CLOSE_LEG":
                targets = self._select_legs(pos, cmd.legs or [0])
                if not targets:
                    log.info(f"[UPDATE #{msg_id}] {cmd} : jambe déjà fermée — rien à faire")
                done += await self._close_many(msg_id, targets, f"Clôture manuelle {cmd}", cmd.only_profit)
                continue

            if cmd.kind == "TP_HIT":
                # Le TP est touché chez le trader (spread différent) : on encaisse si on est en profit,
                # et on n'entre plus en retard (ordres en attente annulés). Le BE des autres
                # jambes est ensuite posé par la boucle BE (fermeture TP1 détectée).
                targets = self._select_legs(pos, cmd.legs or [0])
                done += await self._close_many(msg_id, targets, f"{cmd} — encaissé", only_profit=True)
                done += await self._cancel_pending(msg_id, ords, "TP déjà touché, entrée trop tardive")
                continue

            if cmd.kind == "PARTIAL":
                done += await self._partial_close(msg_id, self._select_legs(pos, cmd.legs), cmd.percent or 50.0)
                continue

            if cmd.kind == "BE":
                targets = self._select_legs(pos, cmd.legs)
                tick = next((tr["tick_size"] for tr in self._tracked_trades), 0.01)
                for t, d in targets.items():
                    be = self._be_price(d["type"], d["price_open"], tick)
                    if self._already_protected(d["type"], d["sl"], be):
                        continue
                    ok = await self._mt5.modify_position_sltp(t, sl=be)
                    done += bool(ok)
                    log.info(f"{'✅' if ok else '❌'} [UPDATE #{msg_id}] BE ticket #{t} SL → {be}")
                if ords and not cmd.legs:
                    log.info(f"[UPDATE #{msg_id}] BE ignoré pour {len(ords)} ordre(s) pas encore déclenché(s)")
                continue

            if cmd.kind == "SL":
                for t, d in {**pos, **ords}.items():
                    sl = resolve_price(cmd.value, d["price_open"])
                    if abs(sl - (d["sl"] or 0)) < 1e-6 or not self._sane(sl, d["price_open"], "SL", msg_id):
                        continue
                    if t in pos and d["sl"]:
                        be = self._be_price(d["type"], d["price_open"], 0.01)
                        worse = sl < d["sl"] if d["type"] == "BUY" else sl > d["sl"]
                        if self._already_protected(d["type"], d["sl"], be) and worse:
                            log.info(f"[UPDATE #{msg_id}] SL {sl} ignoré pour #{t} : déjà protégé au BE ({d['sl']})")
                            continue
                    if t in pos:
                        ok = await self._mt5.modify_position_sltp(t, sl=sl)
                    else:
                        ok = await self._mt5.modify_pending_order(t, sl=sl)
                    done += bool(ok)
                    log.info(f"{'✅' if ok else '❌'} [UPDATE #{msg_id}] SL ticket #{t} → {sl} (lu: '{cmd.value}')")
                continue

            if cmd.kind == "TP":
                every = {**pos, **ords}
                if cmd.tp_index is not None:
                    targets = {t: d for t, d in every.items() if self._leg_no(d["comment"]) == cmd.tp_index}
                else:
                    # "MODIFIER TP A X" sans numéro = 1re jambe avec un TP chiffré encore active
                    numbered = sorted(((t, d) for t, d in every.items() if d.get("tp")),
                                      key=lambda kv: self._leg_no(kv[1]["comment"]))
                    targets = dict(numbered[:1])
                if not targets:
                    log.info(f"[UPDATE #{msg_id}] {cmd} : aucune jambe correspondante encore active")
                for t, d in targets.items():
                    tp = resolve_price(cmd.value, d["price_open"])
                    if not self._sane(tp, d["price_open"], "TP", msg_id):
                        continue
                    if t in pos:
                        ok = await self._mt5.modify_position_sltp(t, tp=tp)
                    else:
                        ok = await self._mt5.modify_pending_order(t, tp=tp)
                    done += bool(ok)
                    log.info(f"{'✅' if ok else '❌'} [UPDATE #{msg_id}] TP ticket #{t} → {tp}")
        return done

    async def expire_pending_orders(self, max_age_minutes: float) -> int:
        """
        Annule les ordres en attente du bot plus vieux que `max_age_minutes`.
        Un ordre au bord de la zone qui n'est toujours pas déclenché longtemps après
        le signal correspond à un setup que le canal a abandonné.
        """
        if not max_age_minutes or max_age_minutes <= 0:
            return 0
        try:
            state = await self._mt5.get_active_state()
        except Exception:
            return 0
        bot_orders = {t: d for t, d in state["orders"].items()
                      if re.match(r"^TG#\d+_", d.get("comment") or "")
                      and d.get("magic", MT5Bridge.BOT_MAGIC) == MT5Bridge.BOT_MAGIC}
        if not bot_orders:
            return 0
        now = await self._mt5.get_server_time(next(iter(bot_orders.values())).get("symbol", "XAUUSD"))
        if not now:
            return 0
        cancelled = set()
        for t, d in bot_orders.items():
            setup = d.get("time_setup")
            if setup and now - setup > max_age_minutes * 60:
                if await self._mt5.cancel_order(t):
                    cancelled.add(t)
                    log.info(f"⌛ Ordre en attente #{t} ({d.get('comment')}) annulé : pas déclenché après "
                             f"{(now - setup) / 60:.0f} min (BOT_PENDING_EXPIRY_MINUTES={max_age_minutes:g})")
        if cancelled:
            self._forget_tracking(cancelled)
        return len(cancelled)

    async def latest_signal_with_trades(self) -> Optional[int]:
        """msg_id du signal le plus récent qui a encore des positions/ordres du bot sur MT5."""
        try:
            state = await self._mt5.get_active_state()
        except Exception:
            return None
        ids = []
        for kind in ("positions", "orders"):
            for d in state[kind].values():
                m = re.match(r"^TG#(\d+)_", d.get("comment") or "")
                if m and d.get("magic", MT5Bridge.BOT_MAGIC) == MT5Bridge.BOT_MAGIC:
                    ids.append(int(m.group(1)))
        return max(ids) if ids else None

    def _reject(self, signal: TradingSignal, reason: str) -> TradingSignal:
        log.warning(f"⛔ Signal rejeté — {reason}")
        return signal.model_copy(update={"status": SignalStatus.REJECTED, "error_message": reason})

    def _mark_failed(self, signal: TradingSignal, error: str) -> TradingSignal:
        log.error(f"❌ Ordre échoué — {error}")
        return signal.model_copy(update={"status": SignalStatus.FAILED, "error_message": error})
