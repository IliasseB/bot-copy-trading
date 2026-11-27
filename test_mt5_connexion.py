"""
test_mt5_connexion.py
Script de test AUTONOME — ne touche pas à Telegram.
Vérifie étape par étape que MT5 fonctionne et peut passer un ordre réel.

USAGE : double-clique sur TEST_MT5.bat (ou lance "python test_mt5_connexion.py")
"""
from __future__ import annotations

import asyncio
import time

from config import get_config
from execution.mt5_bridge import MT5Bridge
from execution.trade_executor import TradeExecutor
from parser.regex_parser import RegexParser
from risk_management.risk_manager import RiskManager


def line(txt=""):
    print(txt)


def step(n, title):
    line()
    line(f"{'='*60}")
    line(f"  ÉTAPE {n} — {title}")
    line(f"{'='*60}")


async def main():
    line("#" * 60)
    line("#  TEST COMPLET DU BOT — VÉRIFICATION MT5 + PIPELINE")
    line("#" * 60)

    cfg = get_config()

    # ── ÉTAPE 1 : Connexion MT5 ──────────────────────────────────────────
    step(1, "CONNEXION À MT5")
    mt5 = MT5Bridge(cfg.mt5)
    connected = await mt5.connect()

    if not connected:
        line("❌ ÉCHEC — Impossible de se connecter à MT5")
        line("   Vérifie que MT5 Pepperstone est OUVERT et connecté")
        line("   Vérifie que 'Algo Trading' est activé (bouton en haut de MT5)")
        return

    line("✅ MT5 connecté avec succès")

    # ── ÉTAPE 2 : Infos compte ────────────────────────────────────────────
    step(2, "INFOS DU COMPTE")
    balance = await mt5.get_balance()
    line(f"💰 Balance : {balance} EUR")

    if balance <= 0:
        line("⚠️  ATTENTION : balance à 0, vérifie ton compte démo")

    # ── ÉTAPE 3 : Détection du symbole XAUUSD ────────────────────────────
    step(3, "DÉTECTION DU SYMBOLE OR (XAUUSD)")
    real_symbol = mt5._resolve_symbol("XAUUSD")
    line(f"📍 Symbole détecté sur ce compte : '{real_symbol}'")

    try:
        current_price = await mt5.get_current_price(real_symbol, "BUY")
        line(f"💹 Prix actuel de l'or : {current_price}")
    except Exception as e:
        line(f"❌ ÉCHEC — Impossible de lire le prix : {e}")
        line("   Le symbole n'est peut-être pas actif dans Market Watch sur MT5")
        line("   Ouvre MT5 → Market Watch → clic droit → Show All")
        return

    # ── ÉTAPE 4 : Lecture du spread ──────────────────────────────────────
    step(4, "LECTURE DU SPREAD")
    spread = await mt5.get_spread_points(real_symbol)
    line(f"📊 Spread actuel : {spread} points")

    if spread >= 200:
        line("⚠️  ATTENTION : spread très large, le marché est peut-être fermé")
        line("   (Le forex/or ferme le week-end)")

    # ── ÉTAPE 5 : Test du parser avec un faux signal ──────────────────────
    step(5, "TEST DU PARSER (simulation d'un signal Telegram)")

    fake_signal_text = f"""🚨 BUY XAUUSD **__

__****__➡️__****__ENTRY : {current_price-3:.0f}-{current_price+3:.0f}

__****__🔴__****__SL : {current_price-10:.0f}

__****__🟢__****__TP1: {current_price+10:.0f}
__****__🟢__****__TP2: OPEN

__**⚠️**__Disclaimer test"""

    line("📝 Texte simulé envoyé au parser :")
    line(f"   {fake_signal_text[:80]}...")

    parser = RegexParser()
    signal, confidence = parser.parse(fake_signal_text, msg_id=99999, chat_id=-2422625648)

    if signal is None:
        line("❌ ÉCHEC — Le parser n'a pas réussi à extraire le signal !")
        line("   C'est un problème de regex, contacte le développeur")
        return

    line("✅ Signal extrait avec succès :")
    line(f"   Direction : {signal.direction}")
    line(f"   Symbole   : {signal.symbol}")
    line(f"   Zone      : {signal.entry_low} - {signal.entry_high}")
    line(f"   SL        : {signal.stop_loss}")
    line(f"   TP        : {signal.take_profits}")
    line(f"   Confiance : {confidence:.2f}")

    # ── ÉTAPE 6 : Demande confirmation avant d'exécuter un VRAI ordre ────
    step(6, "TEST D'EXÉCUTION RÉELLE SUR MT5 (compte DÉMO)")
    line("⚠️  Ce test va placer un VRAI ordre sur ton compte DÉMO Pepperstone")
    line("   Lot : 0.01 (minimum)")
    line(f"   Direction : {signal.direction}")
    line(f"   Prix approximatif : {current_price}")
    line()
    reponse = input("Veux-tu continuer et placer un ordre de test ? (oui/non) : ").strip().lower()

    if reponse not in ("oui", "o", "yes", "y"):
        line()
        line("🛑 Test arrêté avant l'exécution — c'est ton choix, aucun ordre placé.")
        line()
        line("✅ RÉSUMÉ : MT5 fonctionne, le parser fonctionne.")
        line("   Le bot est prêt à recevoir de vrais signaux Telegram.")
        return

    risk_cfg = cfg.risk
    risk = RiskManager(risk_cfg, mt5)
    executor = TradeExecutor(
        mt5=mt5,
        risk=risk,
        db_writer=None,
        confidence_threshold=0.25,
    )

    line()
    line("⏳ Exécution en cours...")
    t0 = time.time_ns()
    result = await executor.execute(signal)
    total_ms = (time.time_ns() - t0) / 1_000_000

    line()
    if result.status.value == "EXECUTED" if hasattr(result.status, 'value') else result.status == "EXECUTED":
        line("🎯 SUCCÈS TOTAL — ORDRE PLACÉ SUR MT5 !")
        line(f"   Ticket      : {result.ticket}")
        line(f"   Prix rempli : {result.filled_price}")
        line(f"   Lot         : {result.lot_size}")
        line(f"   Temps total : {total_ms:.0f}ms")
        line()
        line("👉 Va vérifier dans MT5 → onglet 'Trade' pour voir la position ouverte !")
        line("👉 Tu peux la fermer manuellement dans MT5 si c'était juste un test.")
    else:
        line("❌ ÉCHEC DE L'EXÉCUTION")
        line(f"   Statut : {result.status}")
        line(f"   Raison : {result.error_message}")

    await mt5.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Test interrompu")
    except Exception as e:
        print(f"\n❌ Erreur inattendue : {e}")
        import traceback
        traceback.print_exc()

    print()
    input("Appuie sur Entrée pour fermer...")
