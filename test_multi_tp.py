"""
test_multi_tp.py - Script de test autonome pour vérifier le découpage Multi-TP et le Break-Even.
"""
import asyncio
import sys

from config import get_config
from execution.mt5_bridge import MT5Bridge
from execution.trade_executor import TradeExecutor
from parser.signal_model import Direction, OrderType, TradingSignal
from risk_management.risk_manager import RiskManager
from utils.logger import get_logger, setup_logging

log = get_logger("test_be")

async def run_test():
    print("=" * 60)
    print(" 🧪 TEST AUTONOME : MULTI-TP & BREAK-EVEN (BE)")
    print("=" * 60)

    # 1. Initialisation
    cfg = get_config()
    setup_logging("INFO")

    mt5 = MT5Bridge(cfg.mt5)
    await mt5.connect()

    risk = RiskManager(cfg.risk, mt5)
    # On force un lot par défaut un peu plus gros pour être sûr de pouvoir le diviser par 3
    cfg.risk.default_lot_size = 0.09

    executor = TradeExecutor(mt5=mt5, risk=risk, confidence_threshold=0.0)

    # 2. Récupération du prix actuel pour créer un signal réaliste
    try:
        current_price = await mt5.get_current_price("XAUUSD", "BUY")
    except Exception as e:
        log.error(f"Impossible de lire le prix de l'or. MT5 est-il bien connecté ? {e}")
        return

    print(f"\n📈 Prix actuel de l'or (XAUUSD) : {current_price}")

    # 3. Création d'un faux signal Telegram injecté directement dans l'Executor
    # On met un TP1 très serré (+0.50$), un TP2 normal, et un TP3 OPEN (0.0)
    test_signal = TradingSignal(
        telegram_msg_id=99999,
        telegram_chat_id=-1,
        raw_text="SIGNAL TEST AUTO",
        direction=Direction.BUY,
        symbol="XAUUSD",
        order_type=OrderType.MARKET,
        entry=current_price,
        stop_loss=current_price - 5.0,        # SL à 5 dollars en dessous
        take_profits=[
            current_price + 0.5,              # TP1 (très proche)
            current_price + 3.0,              # TP2
            0.0                            # TP3 (Sera converti en 0.0)
        ],
        confidence=1.0
    )

    print("\n🚀 1. ENVOI DE L'ORDRE MULTI-TP (Divisé en 3)...")
    result = await executor.execute(test_signal)

    if result.status != "EXECUTED":
        print(f"❌ Échec de l'exécution : {result.error_message}")
        return

    print("\n⏳ 2. DÉMARRAGE DE LA BOUCLE DE SURVEILLANCE BREAK-EVEN...")
    print("💡 ASTUCE POUR GAGNER DU TEMPS :")
    print("   Va dans ton terminal MT5, onglet 'Historique/Trading'.")
    print("   Ferme MANUELLEMENT la position TP1 en cliquant sur la croix.")
    print("   Le bot va détecter la fermeture et passer les deux autres positions à Break-Even !")
    print("   (Appuie sur CTRL+C pour arrêter le script)\n")

    # 4. Boucle de surveillance infinie (identique à main.py)
    try:
        while True:
            await executor.monitor_and_adjust_be()
            await asyncio.sleep(1.0)
            sys.stdout.write(".")
            sys.stdout.flush()
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        print("\n🛑 Test arrêté manuellement.")
    finally:
        await mt5.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(run_test())
    except KeyboardInterrupt:
        print("\n🛑 Test annulé.")
