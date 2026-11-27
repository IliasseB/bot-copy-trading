"""
main.py - Point d'entrée du bot augmenté avec boucle de monitoring Multi-TP (BE).
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Dict, Optional

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    UVLOOP_ACTIVE = True
except ImportError:
    UVLOOP_ACTIVE = False

from config import get_config, reveal
from database.db import Database
from execution.mt5_bridge import MT5Bridge
from execution.trade_executor import TradeExecutor
from parser.regex_parser import looks_like_signal
from parser.signal_model import SignalStatus
from parser.signal_parser import SignalParser
from parser.update_parser import parse_update, parse_update_no_reply
from risk_management.risk_manager import RiskManager
from telegram_listener.listener import TelegramListener
from utils.config_check import check_config
from utils.heartbeat import write_heartbeat
from utils.instance_lock import AlreadyRunningError, InstanceLock
from utils.logger import get_logger, setup_logging
from utils.notifier import Notifier
from utils.report import format_daily_report
from utils.state_store import StateStore

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    aioredis = None
    REDIS_AVAILABLE = False

log = get_logger("main")


class TradingBot:
    def __init__(self):
        self._cfg       = get_config()
        self._db        = None
        self._redis     = None
        self._mt5       = None
        self._risk      = None
        self._parser    = None
        self._executor  = None
        self._listener  = None
        self._pending_orders: Dict[int, int] = {}
        # Fil des réponses : message → signal d'origine (réponse à une réponse, réponse à la
        # pré-alerte "XAUUSD BUY NOW" dont le signal complet est lui-même une réponse...)
        self._reply_root: Dict[int, int] = {}
        self._last_signal_msg_id: Optional[int] = None   # dernier signal vu sur le canal
        # Mémoire sur disque : après un redémarrage, les réponses et ordres sans "répondre"
        # retrouvent toujours leur signal
        self._restore(StateStore())
        self._notifier = Notifier(enabled=bool(getattr(self._cfg.bot, "notify", False)))
        # Tâche d'arrière-plan pour surveiller le Break-Even
        self._monitor_task: Optional[asyncio.Task] = None

    async def startup(self) -> None:
        tg, m5 = self._cfg.telegram, self._cfg.mt5
        setup_logging(self._cfg.bot.log_level,
                      secrets=[tg.phone, tg.phone.lstrip("+"), reveal(tg.api_hash), reveal(m5.password)])
        log.info(f"=== BOT DÉMARRAGE === env={self._cfg.bot.env} uvloop={UVLOOP_ACTIVE}")
        r, b = self._cfg.risk, self._cfg.bot
        lot = f"{r.lot_per_tp} par TP (max {r.max_lot})" if r.lot_per_tp > 0 else f"{r.fixed_lot or 'auto'} (max {r.max_lot})"
        log.info(f"[CONFIG] Lot : {lot} — trades simultanés max {r.max_simultaneous_trades} — "
                 f"perte max/jour {str(r.max_daily_loss_percent) + ' %' if r.max_daily_loss_percent else 'aucune'}")
        log.info(f"[CONFIG] Horaires : {r.trading_hours or 'toujours'} / jours {r.trading_days or 'tous'} — "
                 f"max {r.max_trades_per_day or '∞'} signal(aux)/jour — notifications {'oui' if b.notify else 'non'}")
        problems = check_config(self._cfg)
        for level, msg in problems:
            (log.error if level == "ERREUR" else log.warning if level == "ATTENTION" else log.info)(f"[CONFIG] {msg}")
        if any(level == "ERREUR" for level, _ in problems):
            raise RuntimeError("Configuration invalide dans .env — corrige les lignes [CONFIG] ci-dessus")

        try:
            self._db = Database(self._cfg.database.database_url)
            await self._db.connect()
            log.info("PostgreSQL connecté")
        except Exception as e:
            log.warning(f"PostgreSQL non disponible: {e}")
            self._db = None

        if REDIS_AVAILABLE:
            try:
                self._redis = aioredis.from_url(
                    self._cfg.database.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=3,
                )
                await self._redis.ping()
                log.info("Redis connecté")
            except Exception as e:
                log.warning(f"Redis non disponible: {e}")
                self._redis = None

        self._mt5 = MT5Bridge(self._cfg.mt5)
        if not await self._mt5.connect():
            # Avant : le bot continuait "PRÊT" même sans MT5. Maintenant on s'arrête net.
            raise RuntimeError(
                "Connexion MT5 impossible — bot arrêté. Regarde l'erreur 'mt5' juste au-dessus."
            )

        self._risk     = RiskManager(self._cfg.risk, self._mt5)
        self._parser   = SignalParser(dedup_cache=self._redis)
        self._executor = TradeExecutor(
            mt5=self._mt5,
            risk=self._risk,
            db_writer=self._db,
            confidence_threshold=self._cfg.bot.signal_confidence_threshold,
            be_offset_points=self._cfg.execution.be_offset_points,
            max_entry_distance_percent=self._cfg.risk.max_entry_distance_percent,
            partial_close_enabled=self._cfg.bot.update_partial_close,
            max_update_distance_percent=self._cfg.bot.update_max_distance_percent,
        )
        # Reprend le suivi BE des trades déjà ouverts (si le bot a redémarré)
        await self._executor.recover_tracked_trades()
        self._listener = TelegramListener(
            config=self._cfg.telegram,
            on_message=self._on_telegram_message,
            on_ready=lambda: self._notifier.attach(self._listener.send_to_self),
        )

        # 🚀 LANCEMENT DE LA BOUCLE DE SURVEILLANCE BE EN ARRIÈRE-PLAN
        self._monitor_task = asyncio.create_task(self._monitor_loop())

        log.info("=== BOT PRÊT — En attente de signaux... ===")
        self._notify("✅ Bot démarré — en écoute du canal")

    async def _monitor_loop(self) -> None:
        """Boucle d'arrière-plan autonome vérifiant le statut des TP toutes les secondes."""
        log.info("🔄 Boucle de monitoring Multi-TP (BE) démarrée")
        expiry = float(getattr(self._cfg.bot, "pending_expiry_minutes", 0) or 0)
        tick = 0
        while True:
            try:
                if self._executor:
                    await self._executor.monitor_and_adjust_be()
                    if expiry > 0 and tick % 15 == 0:          # ordres en attente : vérif toutes les 15 s
                        await self._executor.expire_pending_orders(expiry)
                if tick % 30 == 0:
                    await self._maybe_send_daily_report()
                    self._write_heartbeat()
            except Exception as e:
                log.error(f"Erreur dans la boucle de monitoring BE: {e}")
            tick += 1
            await asyncio.sleep(1.0)  # Fréquence de rafraîchissement d'une seconde

    async def shutdown(self) -> None:
        log.info("Arrêt du bot...")
        # Arrêt propre de la boucle de monitoring
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        notifier = getattr(self, "_notifier", None)
        if notifier:
            await notifier.flush()
            notifier.stop()
        if self._listener:  await self._listener.stop()
        if self._mt5:       await self._mt5.disconnect()
        if self._db:        await self._db.disconnect()
        if self._redis:     await self._redis.aclose()

    async def run(self) -> None:
        await self.startup()
        try:
            await self._listener.start()
        finally:
            await self.shutdown()

    # ── Pipeline principal ────────────────────────────────────────────────────

    async def _on_telegram_message(
        self, text: str, msg_id: int, chat_id: int, is_edit: bool,
        reply_to: Optional[int] = None, age_s: float = 0.0,
    ) -> None:
        t0 = time.time_ns()

        log.info(f"--- Message {'[EDIT]' if is_edit else '[NEW] '} id={msg_id}"
                 f"{f' (réponse à #{reply_to})' if reply_to else ''} ---")
        log.info(f"Texte complet: {text[:200]}")

        if is_edit:
            await self._handle_edit(text, msg_id, chat_id, t0, age_s)
            return

        # Remonte le fil : réponse à une réponse → signal d'origine
        root = self._reply_root.get(reply_to, reply_to) if reply_to else None
        if reply_to:
            self._reply_root[msg_id] = root
            if len(self._reply_root) > 5000:
                self._reply_root = dict(list(self._reply_root.items())[-2500:])
            self._persist()

        is_signal = looks_like_signal(text)

        # ── Signal complet posté EN RÉPONSE (à la pré-alerte "XAUUSD BUY NOW", ou
        #    "UPDATE VENTE GOLD" en réponse au signal d'avant) ─────────────────
        if is_signal and root and await self._executor.has_trades_for(root):
            log.info(f"[{msg_id}] Signal re-posté en réponse à #{root} qui a déjà des trades → mise à jour de #{root}")
            await self._handle_edit(text, root, chat_id, t0, age_s)
            return

        # ── Réponse (non-signal) = message de gestion (SL, BE, TP1 touché, clôture...) ──
        if root and not is_signal:
            await self._handle_update(text, root, msg_id)
            return

        # ── Message SANS réponse : ordre clair → dernier signal encore ouvert ──
        if not root and not is_signal and self._cfg.bot.update_no_reply_fallback:
            if await self._handle_update_no_reply(text, msg_id):
                return

        # ── Nouveau signal ─────────────────────────────────────────────────
        if is_signal:
            self._last_signal_msg_id = msg_id
            if reply_to:
                self._reply_root[reply_to] = msg_id   # gestion postée en réponse à la pré-alerte → ce signal
        signal, confidence = await self._parser.parse(text, msg_id, chat_id)

        if signal is None:
            log.info(f"[{msg_id}] Pas un signal — ignoré")
            self._persist()
            return
        self._last_signal_msg_id = msg_id
        self._persist()

        parse_ms = (time.time_ns() - t0) / 1_000_000
        log.info(
            f"✅ SIGNAL DÉTECTÉ — {signal.direction} {signal.symbol} "
            f"zone=[{signal.entry_low}-{signal.entry_high}] "
            f"sl={signal.stop_loss} tp={signal.take_profits} "
            f"conf={confidence:.2f} parse_ms={parse_ms:.1f}ms"
        )
        await self._execute_and_log(signal, t0)

    async def _maybe_send_daily_report(self) -> None:
        """Envoie le récap du jour une fois, à l'heure BOT_DAILY_REPORT_HOUR."""
        from datetime import datetime
        hour = int(getattr(self._cfg.bot, "daily_report_hour", -1))
        now = datetime.now()
        if hour < 0 or now.hour != hour or getattr(self, "_last_report_day", None) == now.date():
            return
        self._last_report_day = now.date()
        if self._mt5 is None or not getattr(self._cfg.bot, "notify", False):
            return
        deals = await self._mt5.get_day_deals()
        self._notify(format_daily_report(deals, now.date()))
        log.info("📊 Récap du jour envoyé sur Telegram")

    def _write_heartbeat(self) -> None:
        try:
            write_heartbeat({
                "last_signal_msg_id": self._last_signal_msg_id,
                "tracked_trades": len(getattr(self._executor, "_tracked_trades", []) or []),
            })
        except OSError as e:
            log.debug(f"Heartbeat non écrit: {e}")

    def _notify(self, text: str) -> None:
        notifier = getattr(self, "_notifier", None)
        if notifier:
            notifier.notify(text)

    def _restore(self, state: StateStore) -> None:
        self._state = state
        saved = state.load()
        self._reply_root = {int(k): int(v) for k, v in saved.get("reply_root", {}).items()}
        self._last_signal_msg_id = saved.get("last_signal_msg_id")

    def _persist(self) -> None:
        state = getattr(self, "_state", None)
        if state is None:
            return
        try:
            recent = dict(list(self._reply_root.items())[-2000:])
            state.save({"reply_root": recent, "last_signal_msg_id": self._last_signal_msg_id})
        except OSError as e:
            log.warning(f"Mémoire du bot non sauvegardée: {e}")

    async def _execute_and_log(self, signal, t0: int, label: str = "TRADES ENVOYÉS") -> None:
        result = await self._executor.execute(signal)
        total_ms = (time.time_ns() - t0) / 1_000_000

        if result.status == SignalStatus.EXECUTED:
            self._pending_orders[signal.telegram_msg_id] = result.ticket
            log.info(
                f"🎯 {label} — Ticket Principal={result.ticket} "
                f"prix={result.filled_price} total={total_ms:.1f}ms"
            )
            tps = ", ".join("OPEN" if tp is None else f"{tp:g}" for tp in (signal.take_profits or [])) or "—"
            self._notify(f"🎯 {signal.direction} {signal.symbol} ouvert à {result.filled_price} "
                         f"(lot {result.lot_size}) — SL {signal.stop_loss} — TP {tps} — signal #{signal.telegram_msg_id}")
        else:
            log.warning(
                f"⛔ Ordre non exécuté — status={result.status} "
                f"raison={result.error_message}"
            )
            self._notify(f"⛔ Signal #{signal.telegram_msg_id} ({signal.direction} {signal.symbol}) non tradé : "
                         f"{result.error_message}")

        if self._db:
            await self._db.save_signal(result)

    async def _handle_update_no_reply(self, text: str, msg_id: int) -> bool:
        """Ordre clair posté sans 'répondre' : appliqué au dernier signal s'il a encore des trades."""
        cmds = parse_update_no_reply(text)
        if not cmds:
            return False
        target = await self._executor.latest_signal_with_trades()
        if target is None:
            log.info(f"[{msg_id}] Ordre sans réponse ({', '.join(map(str, cmds))}) — aucun trade ouvert, ignoré")
            return True
        if target != self._last_signal_msg_id:
            log.warning(
                f"[{msg_id}] Ordre sans réponse ({', '.join(map(str, cmds))}) — ignoré par prudence : "
                f"le dernier signal du canal (#{self._last_signal_msg_id}) n'est pas celui du trade ouvert (#{target})"
            )
            return True
        log.info(f"[{msg_id}] Ordre posté sans 'répondre' → appliqué au dernier signal #{target}")
        return await self._handle_update(text, target, msg_id, cmds=cmds)

    async def _handle_update(self, text: str, signal_msg_id: int, msg_id: int, cmds=None) -> bool:
        """Retourne True si le message était bien une instruction de gestion."""
        cmds = parse_update(text) if cmds is None else cmds
        if not cmds:
            log.info(f"[{msg_id}] Réponse au signal #{signal_msg_id} sans ordre exploitable — ignorée")
            return False
        log.info(f"🔧 MISE À JOUR du signal #{signal_msg_id} — {', '.join(map(str, cmds))}")
        try:
            n = await self._executor.apply_update(signal_msg_id, cmds)
            log.info(f"🔧 {n} modification(s) appliquée(s) sur MT5 pour #{signal_msg_id}")
            if n:
                self._notify(f"🔧 Canal : « {text.strip()[:80]} » → {', '.join(map(str, cmds))} "
                             f"({n} modification(s) sur MT5, signal #{signal_msg_id})")
        except Exception as e:
            log.error(f"Erreur mise à jour #{signal_msg_id}: {e}")
            self._notify(f"❌ Erreur en appliquant « {text.strip()[:60]} » sur #{signal_msg_id} : {e}")
        return True

    async def _handle_edit(
        self, text: str, msg_id: int, chat_id: int, t0: int, age_s: float = 0.0
    ) -> None:
        signal, confidence = self._parser._regex.parse(text, msg_id, chat_id)

        if signal is None:
            log.info(f"[{msg_id}] Message modifié pas un signal — ignoré")
            return

        log.info(
            f"📝 SIGNAL MODIFIÉ — {signal.direction} {signal.symbol} "
            f"zone=[{signal.entry_low}-{signal.entry_high}] "
            f"sl={signal.stop_loss} tp={signal.take_profits} conf={confidence:.2f}"
        )

        # 1) Le bot a déjà des ordres/positions pour ce signal → on les synchronise
        if await self._executor.has_trades_for(msg_id):
            try:
                result = await self._executor.sync_edited_signal(signal)
            except Exception as e:
                log.error(f"Erreur synchro signal modifié #{msg_id}: {e}")
                return
            if result is not None and result.status == SignalStatus.EXECUTED:
                self._pending_orders[msg_id] = result.ticket
                log.info(f"🎯 ORDRES REPLACÉS (après correction) — ticket={result.ticket} prix={result.filled_price}")
            elif result is not None:
                log.warning(f"⛔ Signal corrigé non replacé — raison={result.error_message}")
            return

        # 2) Déjà tradé mais plus rien d'ouvert → trade terminé, on ne rouvre pas
        if msg_id in self._pending_orders:
            log.info(f"[{msg_id}] Signal modifié mais son trade est déjà terminé — ignoré")
            return

        # 3) Jamais tradé (ex: refusé car faute de frappe) → on exécute la version corrigée
        if age_s > self._cfg.bot.max_edit_age_seconds:
            log.info(f"[{msg_id}] Signal modifié trop ancien ({age_s:.0f}s) — pas de nouveau trade")
            return
        await self._execute_and_log(signal, t0, label="ORDRE (après correction) EXÉCUTÉ")


def main() -> None:
    print("=" * 55)
    print("  TELEGRAM SIGNAL BOT — PEPPERSTONE MT5")
    print("=" * 55)
    print("✅ uvloop actif" if UVLOOP_ACTIVE else "⚠️  uvloop non disponible — asyncio standard")

    try:
        lock = InstanceLock().acquire()
    except AlreadyRunningError as e:
        print(f"⛔ {e}")
        sys.exit(2)

    bot = TradingBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\n🛑 Bot arrêté")
    except Exception as e:
        print(f"❌ Erreur fatale: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
