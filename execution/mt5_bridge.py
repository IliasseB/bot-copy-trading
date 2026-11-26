"""
execution/mt5_bridge.py - Async MT5 wrapper pour Pepperstone.
Symbole or = XAUUSD. Auto-détection du symbole ET du mode de remplissage.
"""
from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
from config import MT5Config
from utils.logger import get_logger

log = get_logger("mt5")

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
    log.warning("MetaTrader5 non installé — mode mock actif")


class MT5ConnectionError(Exception):
    pass


class MT5ExecutionError(Exception):
    def __init__(self, message: str, retcode: int = 0):
        super().__init__(message)
        self.retcode = retcode


# Erreurs MT5 pour lesquelles réessayer a du sens (prix qui bouge, timeout...).
# Les autres (Algo Trading coupé 10027, pas assez de marge 10019...) échoueront toujours.
TRANSIENT_RETCODES = {10004, 10012, 10020, 10021, 10024, 10031}

GOLD_SYMBOL_CANDIDATES = ["XAUUSD", "XAUUSDm", "GOLD", "XAU/USD"]


class MT5Bridge:
    def __init__(self, config: MT5Config):
        self._cfg = config
        self._connected = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")
        self._symbol_cache: dict = {}
        self._filling_mode_cache: dict = {}   # symbol → filling mode qui fonctionne
        self._trade_info_cache: dict = {}     # symbol → (horodatage, infos lot/tick) — évite un aller-retour MT5 par signal
        self._last_health_check: float = 0.0

    async def connect(self) -> bool:
        if not MT5_AVAILABLE:
            log.warning("MT5 non disponible — mode mock actif")
            self._connected = True
            return True
        result = await self._run(self._connect_sync)
        self._connected = result
        if result:
            await self._detect_gold_symbol()
            await self._prewarm()
        return result

    async def _prewarm(self) -> None:
        """Charge à l'avance ce dont le 1er signal aura besoin (gagne du temps au moment du trade)."""
        try:
            await self.get_symbol_trade_info("XAUUSD")
        except Exception as e:
            log.warning(f"Pré-chargement infos symbole ignoré: {e}")

    def _connect_sync(self) -> bool:
        kwargs = dict(
            login=self._cfg.login,
            password=self._cfg.password,
            server=self._cfg.server,
            timeout=self._cfg.timeout,
        )
        if self._cfg.path:
            kwargs["path"] = self._cfg.path

        if not mt5.initialize(**kwargs):
            err = mt5.last_error()
            log.error(f"MT5 init échoué: {err}")
            return False

        info = mt5.account_info()
        if info is None:
            log.error(f"MT5 initialisé mais account_info() vide: {mt5.last_error()}")
            mt5.shutdown()
            return False

        # Le bot doit être connecté au BON compte (login demandé dans le .env)
        if info.login != self._cfg.login:
            log.error(
                f"⛔ Le terminal est connecté au compte {info.login} au lieu de {self._cfg.login} "
                f"— vérifie MT5_LOGIN / MT5_SERVER et MT5_PATH (un terminal par compte)."
            )
            mt5.shutdown()
            return False

        # trade_mode : 0 = DEMO, 1 = CONTEST, 2 = REAL
        is_real = info.trade_mode == getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2)
        mode_label = "RÉEL 💶" if is_real else "DÉMO"
        log.info(
            f"MT5 connecté — compte {mode_label} login={info.login} server={info.server} "
            f"balance={info.balance} {info.currency} trade_allowed={info.trade_allowed}"
        )

        if is_real and not self._cfg.allow_real:
            log.error(
                "⛔ COMPTE RÉEL détecté mais MT5_ALLOW_REAL n'est pas à true dans le .env. "
                "Connexion refusée par sécurité — aucun ordre ne sera passé."
            )
            mt5.shutdown()
            return False
        if is_real:
            log.warning("⚠️⚠️⚠️  MODE ARGENT RÉEL ACTIF — chaque signal passera un vrai ordre ⚠️⚠️⚠️")

        if not info.trade_allowed:
            log.error(
                "⚠️⚠️⚠️  TRADING NON AUTORISÉ SUR CE COMPTE ⚠️⚠️⚠️ "
                "Va dans MT5 → clique sur le bouton 'Algo Trading' en haut "
                "(doit être VERT/actif) et vérifie Outils→Options→Expert Advisors "
                "→ 'Allow algorithmic trading' coché."
            )

        # Vérifie aussi le terminal_info pour AutoTrading
        term = mt5.terminal_info()
        if term and not term.trade_allowed:
            log.error(
                "⚠️⚠️⚠️  ALGO TRADING DÉSACTIVÉ DANS LE TERMINAL MT5 ⚠️⚠️⚠️ "
                "Clique sur le bouton 'Algo Trading' en haut de MT5 (doit devenir vert)"
            )

        return True

    async def _detect_gold_symbol(self) -> None:
        if not MT5_AVAILABLE:
            return

        def _detect():
            for candidate in GOLD_SYMBOL_CANDIDATES:
                info = mt5.symbol_info(candidate)
                if info is not None:
                    mt5.symbol_select(candidate, True)
                    return candidate, info.filling_mode
            return None, None

        found, filling_mode = await self._run(_detect)
        if found:
            self._symbol_cache["XAUUSD"] = found
            log.info(f"✅ Symbole or détecté sur MT5: '{found}' (filling_mode={filling_mode})")
        else:
            self._symbol_cache["XAUUSD"] = "XAUUSD"
            log.warning("Symbole or non détecté automatiquement — utilisation de XAUUSD par défaut")

    def _resolve_symbol(self, symbol: str) -> str:
        normalized = symbol.upper().replace("M", "") if symbol.upper().endswith("M") else symbol.upper()
        return self._symbol_cache.get(normalized, symbol)

    async def disconnect(self) -> None:
        if MT5_AVAILABLE and self._connected:
            await self._run(mt5.shutdown)
        self._connected = False

    HEALTH_CHECK_INTERVAL_S = 2.0

    async def ensure_connected(self) -> None:
        if not MT5_AVAILABLE:
            self._connected = True
            return

        now = time.monotonic()
        if self._connected and (now - self._last_health_check) < self.HEALTH_CHECK_INTERVAL_S:
            return  # vérifié il y a moins de 2s — on ne revérifie pas à chaque appel

        healthy = await self._run(self._check_health)
        self._last_health_check = now
        if not healthy:
            log.warning("⚠️ Connexion MT5 perdue (account_info introuvable) — tentative de reconnexion...")
            self._connected = False
            ok = await self.connect()
            if not ok:
                raise MT5ConnectionError("Impossible de se reconnecter à MT5")

    @staticmethod
    def _check_health() -> bool:
        try:
            return mt5.account_info() is not None
        except Exception:
            return False

    async def get_market_snapshot(self, symbol: str, direction: str) -> dict:
        """
        Lit balance + spread + prix actuel en UN SEUL aller-retour vers le thread MT5,
        au lieu de 3 appels séparés (économise ~2 hops de thread executor par trade).
        """
        await self.ensure_connected()
        real_symbol = self._resolve_symbol(symbol)

        if not MT5_AVAILABLE:
            return {"balance": 50000.0, "spread": 10.0, "price": 4065.0}

        def _snapshot():
            info = mt5.account_info()
            tick = mt5.symbol_info_tick(real_symbol)
            sym_info = mt5.symbol_info(real_symbol)

            if info is None:
                return None

            balance = float(info.balance)

            spread = 20.0
            if tick and sym_info and sym_info.point > 0:
                spread = round((tick.ask - tick.bid) / sym_info.point, 1)

            price = None
            if tick:
                price = tick.ask if direction == "BUY" else tick.bid

            return {"balance": balance, "spread": spread, "price": price}

        result = await self._run(_snapshot)
        if result is None:
            self._connected = False
            raise MT5ConnectionError("account_info() a renvoyé None — MT5 déconnecté")
        if result["price"] is None:
            raise MT5ExecutionError(f"Impossible d'obtenir le prix pour {real_symbol}")
        return result

    async def get_balance(self) -> float:
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            return 50000.0
        info = await self._run(mt5.account_info)
        if info is None:
            self._connected = False
            raise MT5ConnectionError("account_info() a renvoyé None — MT5 déconnecté")
        return float(info.balance)

    async def get_spread_points(self, symbol: str) -> float:
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            return 10.0
        real_symbol = self._resolve_symbol(symbol)

        def _get_spread():
            tick = mt5.symbol_info_tick(real_symbol)
            info = mt5.symbol_info(real_symbol)
            if tick and info and info.point > 0:
                return round((tick.ask - tick.bid) / info.point, 1)
            return None

        spread = await self._run(_get_spread)
        if spread is None:
            log.warning(f"Impossible de lire le spread pour '{real_symbol}' — défaut 20")
            return 20.0
        return spread

    async def get_current_price(self, symbol: str, direction: str) -> float:
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            return 4065.0
        real_symbol = self._resolve_symbol(symbol)

        def _get_price():
            tick = mt5.symbol_info_tick(real_symbol)
            if not tick:
                return None
            return tick.ask if direction == "BUY" else tick.bid

        price = await self._run(_get_price)
        if price is None:
            raise MT5ExecutionError(f"Impossible d'obtenir le prix pour {real_symbol}")
        return price

    def _get_filling_modes_to_try(self, symbol: str) -> list:
        """Retourne la liste des modes de remplissage à essayer, dans l'ordre."""
        if symbol in self._filling_mode_cache:
            return [self._filling_mode_cache[symbol]]
        # Ordre de test: IOC (le plus courant), puis FOK, puis RETURN
        return [mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_RETURN]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.1, max=2),
        retry=retry_if_exception(
            lambda e: isinstance(e, MT5ExecutionError) and e.retcode in TRANSIENT_RETCODES
        ),
        reraise=True,
    )
    async def send_market_order(
        self, symbol: str, direction: str, lot: float,
        sl: Optional[float], tp: Optional[float],
        comment: str = "TG_BOT", deviation: int = 20,
        known_price: Optional[float] = None,
    ) -> dict:
        await self.ensure_connected()
        t0 = time.time_ns()
        real_symbol = self._resolve_symbol(symbol)

        if not MT5_AVAILABLE:
            return self._mock_order(real_symbol, direction, lot, sl, tp, t0)

        # Si le prix a déjà été récupéré juste avant (ex: via get_market_snapshot),
        # on évite un aller-retour MT5 redondant.
        price = known_price if known_price is not None else await self.get_current_price(real_symbol, direction)
        order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

        def _send_with_filling_modes():
            """Essaie plusieurs modes de remplissage jusqu'à ce qu'un fonctionne."""
            last_error = None
            for filling in self._get_filling_modes_to_try(real_symbol):
                request = {
                    "action":       mt5.TRADE_ACTION_DEAL,
                    "symbol":       real_symbol,
                    "volume":       lot,
                    "type":         order_type,
                    "price":        price,
                    "sl":           sl or 0.0,
                    "tp":           tp or 0.0,
                    "deviation":    deviation,
                    "magic":        self.BOT_MAGIC,
                    "comment":      comment,
                    "type_time":    mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                }
                result = mt5.order_send(request)
                mt5_err = mt5.last_error()

                if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                    # Ce mode fonctionne, on le retient pour la prochaine fois
                    self._filling_mode_cache[real_symbol] = filling
                    return result, None

                # Log l'échec pour ce mode
                err_detail = f"filling={filling} result={result} mt5_last_error={mt5_err}"
                log.warning(f"Tentative échouée — {err_detail}")
                last_error = err_detail

                # Si result existe avec un retcode précis (pas juste None),
                # inutile d'essayer d'autres modes sauf si c'est justement
                # une erreur de filling mode (retcode 10030 = Unsupported filling mode)
                if result is not None and result.retcode != 10030:
                    return result, last_error

            return None, last_error

        result, error_detail = await self._run(_send_with_filling_modes)
        exec_ms = (time.time_ns() - t0) / 1_000_000

        if result is None:
            raise MT5ExecutionError(
                f"Ordre échoué — aucun mode de remplissage n'a fonctionné. "
                f"Détails: {error_detail}. "
                f"VÉRIFIE: 1) Bouton 'Algo Trading' actif dans MT5 (vert), "
                f"2) Marché ouvert (pas le week-end), "
                f"3) Symbole {real_symbol} bien sélectionné dans Market Watch",
                retcode=-1
            )

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5ExecutionError(
                f"Ordre échoué retcode={result.retcode} comment={result.comment}",
                retcode=result.retcode
            )

        log.info(
            f"✅ Ordre envoyé — ticket={result.order} {direction} {real_symbol} "
            f"lot={lot} prix={result.price} sl={sl} tp={tp} exec_ms={exec_ms:.0f}ms"
        )
        return {"ticket": result.order, "filled_price": result.price, "lot": lot, "exec_ms": exec_ms}

    async def send_pending_order(
        self, symbol: str, direction: str, order_type: str,
        lot: float, entry: float, sl: Optional[float], tp: Optional[float],
        comment: str = "TG_BOT_PENDING"
    ) -> dict:
        await self.ensure_connected()
        t0 = time.time_ns()
        real_symbol = self._resolve_symbol(symbol)

        if not MT5_AVAILABLE:
            return self._mock_order(real_symbol, direction, lot, sl, tp, t0)

        current_price = await self.get_current_price(real_symbol, direction)

        if direction == "BUY":
            mt5_type = mt5.ORDER_TYPE_BUY_LIMIT if entry < current_price else mt5.ORDER_TYPE_BUY_STOP
        else:
            mt5_type = mt5.ORDER_TYPE_SELL_LIMIT if entry > current_price else mt5.ORDER_TYPE_SELL_STOP

        def _send_with_filling_modes():
            last_error = None
            for filling in self._get_filling_modes_to_try(real_symbol):
                request = {
                    "action":       mt5.TRADE_ACTION_PENDING,
                    "symbol":       real_symbol,
                    "volume":       lot,
                    "type":         mt5_type,
                    "price":        entry,
                    "sl":           sl or 0.0,
                    "tp":           tp or 0.0,
                    "magic":        self.BOT_MAGIC,
                    "comment":      comment,
                    "type_time":    mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                }
                result = mt5.order_send(request)
                mt5_err = mt5.last_error()

                if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                    self._filling_mode_cache[real_symbol] = filling
                    return result, None

                err_detail = f"filling={filling} result={result} mt5_last_error={mt5_err}"
                log.warning(f"Tentative pending échouée — {err_detail}")
                last_error = err_detail

                if result is not None and result.retcode != 10030:
                    return result, last_error

            return None, last_error

        result, error_detail = await self._run(_send_with_filling_modes)
        exec_ms = (time.time_ns() - t0) / 1_000_000

        if result is None:
            raise MT5ExecutionError(
                f"Ordre pending échoué — aucun mode de remplissage n'a fonctionné. "
                f"Détails: {error_detail}. "
                f"VÉRIFIE: Algo Trading actif + marché ouvert + symbole sélectionné",
                retcode=-1
            )

        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise MT5ExecutionError(
                f"Ordre pending échoué retcode={result.retcode} comment={result.comment}",
                retcode=result.retcode
            )

        log.info(
            f"✅ Ordre pending — ticket={result.order} {direction} {real_symbol} "
            f"@ {entry} sl={sl} tp={tp} exec_ms={exec_ms:.0f}ms"
        )
        return {"ticket": result.order, "filled_price": entry, "lot": lot, "exec_ms": exec_ms}

    async def get_open_position_tickets(self) -> set:
        """Retourne l'ensemble des tickets actuellement ouverts sur le compte."""
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            return set()

        def _get():
            positions = mt5.positions_get()
            if positions is None:
                return set()
            return {p.ticket for p in positions}

        return await self._run(_get)

    BOT_MAGIC = 20240101

    async def get_active_state(self) -> dict:
        """
        UN SEUL aller-retour MT5 pour la boucle BE :
          positions : {ticket: {price_open, sl, tp, type, symbol, comment, magic}}
          orders    : {ticket: {comment, magic, symbol}}   (ordres pending pas encore déclenchés)
        """
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            return {"positions": {}, "orders": {}}

        def _get():
            positions = {}
            for p in (mt5.positions_get() or []):
                positions[p.ticket] = {
                    "price_open": p.price_open, "sl": p.sl, "tp": p.tp,
                    "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                    "symbol": p.symbol, "comment": p.comment, "magic": p.magic,
                    "volume": p.volume, "profit": p.profit, "price_current": p.price_current,
                }
            orders = {}
            buy_types = {mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP,
                         getattr(mt5, "ORDER_TYPE_BUY_STOP_LIMIT", -1)}
            for o in (mt5.orders_get() or []):
                orders[o.ticket] = {
                    "comment": o.comment, "magic": o.magic, "symbol": o.symbol,
                    "price_open": o.price_open, "sl": o.sl, "tp": o.tp,
                    "type": "BUY" if o.type in buy_types else "SELL",
                }
            return {"positions": positions, "orders": orders}

        return await self._run(_get)

    async def get_bot_risk_stats(self) -> Optional[dict]:
        """
        Chiffres de risque lus EN DIRECT sur MT5 (fiables même après un redémarrage) :
          open_signals : nombre de signaux qui ont encore une position ou un ordre en attente
          pnl_today    : gains/pertes RÉALISÉS aujourd'hui par le bot (profit + commission + swap)
        """
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            return None

        def _get():
            import re as _re
            from datetime import datetime as _dt, timedelta as _td
            ids = set()
            for x in list(mt5.positions_get() or []) + list(mt5.orders_get() or []):
                if x.magic == self.BOT_MAGIC:
                    m = _re.match(r"TG#(\d+)_", x.comment or "")
                    ids.add(m.group(1) if m else f"t{x.ticket}")

            start = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
            deals = mt5.history_deals_get(start - _td(hours=3), _dt.now() + _td(hours=3)) or []
            start_ts = start.timestamp()
            bot_positions = {d.position_id for d in deals if d.magic == self.BOT_MAGIC}
            pnl = 0.0
            for d in deals:
                if d.position_id in bot_positions and d.time >= start_ts - 3 * 3600:
                    pnl += d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
            return {"open_signals": len(ids), "pnl_today": round(pnl, 2)}

        try:
            return await self._run(_get)
        except Exception as e:
            log.warning(f"Stats de risque MT5 indisponibles: {e}")
            return None

    async def get_close_reason(self, ticket: int) -> Optional[str]:
        """
        Comment une position s'est fermée : "TP", "SL", "OTHER" (manuel, stop-out...)
        ou None si aucune trace (ex: ordre pending annulé sans jamais être rempli).
        """
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            return "TP"

        def _get():
            deals = mt5.history_deals_get(position=ticket)
            if not deals:
                return None
            for d in reversed(deals):
                if d.entry == mt5.DEAL_ENTRY_OUT or d.entry == getattr(mt5, "DEAL_ENTRY_OUT_BY", -1):
                    if d.reason == mt5.DEAL_REASON_TP:
                        return "TP"
                    if d.reason == mt5.DEAL_REASON_SL:
                        return "SL"
                    return "OTHER"
            return None

        return await self._run(_get)

    async def modify_position_sltp(
        self, ticket: int, sl: Optional[float] = None, tp: Optional[float] = None
    ) -> bool:
        """Modifie SL et/ou TP d'une position ouverte (ex: passage en breakeven)."""
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            log.info(f"[MOCK] SL/TP modifié — ticket={ticket} sl={sl} tp={tp}")
            return True

        def _modify():
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                log.warning(f"Position {ticket} introuvable — déjà fermée ?")
                return False
            pos = positions[0]
            request = {
                "action":   mt5.TRADE_ACTION_SLTP,
                "position": ticket,
                "symbol":   pos.symbol,
                "sl":       sl if sl is not None else pos.sl,
                "tp":       tp if tp is not None else pos.tp,
            }
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                log.warning(f"Modification SL/TP échouée — ticket={ticket} result={result}")
                return False
            return True

        return await self._run(_modify)

    async def cancel_order(self, ticket: int) -> bool:
        """Supprime un ordre en attente (pending)."""
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            log.info(f"[MOCK] Ordre {ticket} annulé")
            return True

        def _cancel():
            result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                log.warning(f"Annulation échouée — ordre={ticket} result={result}")
                return False
            return True

        return await self._run(_cancel)

    async def modify_pending_order(
        self, ticket: int, price: Optional[float] = None,
        sl: Optional[float] = None, tp: Optional[float] = None,
    ) -> bool:
        """Modifie prix d'entrée / SL / TP d'un ordre en attente."""
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            log.info(f"[MOCK] Ordre {ticket} modifié price={price} sl={sl} tp={tp}")
            return True

        def _modify():
            orders = mt5.orders_get(ticket=ticket)
            if not orders:
                log.warning(f"Ordre {ticket} introuvable — déjà déclenché ou annulé ?")
                return False
            o = orders[0]
            request = {
                "action":    mt5.TRADE_ACTION_MODIFY,
                "order":     ticket,
                "symbol":    o.symbol,
                "price":     price if price is not None else o.price_open,
                "sl":        sl if sl is not None else o.sl,
                "tp":        tp if tp is not None else o.tp,
                "type_time": o.type_time,
                "expiration": o.time_expiration,
            }
            result = mt5.order_send(request)
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                log.warning(f"Modification ordre échouée — ordre={ticket} result={result}")
                return False
            return True

        return await self._run(_modify)

    async def close_position(self, ticket: int, deviation: int = 50, volume: Optional[float] = None) -> bool:
        """Ferme une position au prix du marché : entièrement, ou seulement `volume` lot (clôture partielle)."""
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            log.info(f"[MOCK] Position {ticket} fermée{f' (partiel {volume} lot)' if volume else ''}")
            return True

        def _close():
            positions = mt5.positions_get(ticket=ticket)
            if not positions:
                return False
            p = positions[0]
            tick = mt5.symbol_info_tick(p.symbol)
            if tick is None:
                return False
            is_buy = p.type == mt5.POSITION_TYPE_BUY
            for filling in self._get_filling_modes_to_try(p.symbol):
                result = mt5.order_send({
                    "action":       mt5.TRADE_ACTION_DEAL,
                    "position":     ticket,
                    "symbol":       p.symbol,
                    "volume":       min(volume, p.volume) if volume else p.volume,
                    "type":         mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                    "price":        tick.bid if is_buy else tick.ask,
                    "deviation":    deviation,
                    "magic":        self.BOT_MAGIC,
                    "comment":      "TG_CLOSE",
                    "type_time":    mt5.ORDER_TIME_GTC,
                    "type_filling": filling,
                })
                if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                    return True
                if result is not None and result.retcode != 10030:
                    log.warning(f"Fermeture échouée — ticket={ticket} result={result}")
                    return False
            return False

        return await self._run(_close)

    TRADE_INFO_TTL_S = 300.0   # les règles de lot / la valeur du tick bougent très lentement

    async def get_symbol_trade_info(self, symbol: str) -> dict:
        """
        Vraies infos MT5 du symbole, nécessaires au calcul du lot :
        tick_value (valeur d'un tick pour 1 lot, en devise du compte), tick_size,
        volume_min / volume_max / volume_step.
        """
        await self.ensure_connected()
        real_symbol = self._resolve_symbol(symbol)

        if not MT5_AVAILABLE:
            return {
                "tick_value": 1.0, "tick_size": 0.01,
                "volume_min": 0.01, "volume_max": 10.0, "volume_step": 0.01,
            }

        def _get():
            info = mt5.symbol_info(real_symbol)
            if info is None:
                return None
            return {
                "tick_value":  info.trade_tick_value or 1.0,
                "tick_size":   info.trade_tick_size or info.point or 0.01,
                "volume_min":  info.volume_min or 0.01,
                "volume_max":  info.volume_max or 1.0,
                "volume_step": info.volume_step or 0.01,
            }

        cached = self._trade_info_cache.get(real_symbol)
        if cached and (time.monotonic() - cached[0]) < self.TRADE_INFO_TTL_S:
            return cached[1]

        result = await self._run(_get)
        if result is None:
            log.warning(f"symbol_info indisponible pour {real_symbol} — valeurs par défaut prudentes")
            return {
                "tick_value": 1.0, "tick_size": 0.01,
                "volume_min": 0.01, "volume_max": 1.0, "volume_step": 0.01,
            }
        self._trade_info_cache[real_symbol] = (time.monotonic(), result)
        log.info(f"Infos symbole {real_symbol} chargées: {result}")
        return result

    async def get_margin_info(self, symbol: str, direction: str, lot: float, price: float) -> Optional[dict]:
        """
        Marge requise pour ouvrir `lot` (mt5.order_calc_margin) et marge libre du compte.
        Retourne None si MT5 ne peut pas répondre (ex: mode mock).
        """
        await self.ensure_connected()
        if not MT5_AVAILABLE:
            return None
        real_symbol = self._resolve_symbol(symbol)

        def _calc():
            acc = mt5.account_info()
            if acc is None:
                return None
            action = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
            margin = mt5.order_calc_margin(action, real_symbol, lot, price)
            if margin is None:
                return None
            return {"margin_required": float(margin), "free_margin": float(acc.margin_free)}

        return await self._run(_calc)

    async def _run(self, fn, *args):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, fn, *args)

    @staticmethod
    def _mock_order(symbol, direction, lot, sl, tp, t0) -> dict:
        exec_ms = (time.time_ns() - t0) / 1_000_000
        ticket = int(time.time() * 1000) % 1_000_000
        log.info(f"[MOCK] Ordre — ticket={ticket} {direction} {symbol} lot={lot} sl={sl} tp={tp} exec_ms={exec_ms:.0f}ms")
        return {"ticket": ticket, "filled_price": 4065.0, "lot": lot, "exec_ms": exec_ms}
