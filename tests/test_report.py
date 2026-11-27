from datetime import date

from utils.report import format_daily_report

DEALS = [  # la journée réelle du 25/11 : BUY stoppé, puis SELL TP1 + TP2 manuel
    {"signal": 10853, "profit": -4.22, "reason": "SL"},
    {"signal": 10853, "profit": -4.22, "reason": "SL"},
    {"signal": 10860, "profit": 2.91, "reason": "TP"},
    {"signal": 10860, "profit": 3.91, "reason": "manuel"},
]


def test_recap_journee():
    txt = format_daily_report(DEALS, date(2026, 11, 25))
    assert "Récap du 25/11/2026" in txt
    assert "Signaux : 2 — ✅ 1 gagnant(s) — ❌ 1 perdant(s)" in txt
    assert "Résultat : -1.62 €" in txt
    assert "❌ #10853 : -8.44 € (SL + SL)" in txt
    assert "✅ #10860 : +6.82 € (TP + manuel)" in txt


def test_journee_vide():
    assert "Aucun trade" in format_daily_report([], date(2026, 11, 26))


async def test_recap_envoye_une_seule_fois_a_l_heure_prevue():
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace

    from main import TradingBot
    from utils.notifier import Notifier

    class MT5:
        calls = 0

        async def get_day_deals(self):
            MT5.calls += 1
            return DEALS

    bot = TradingBot.__new__(TradingBot)
    bot._cfg = SimpleNamespace(bot=SimpleNamespace(daily_report_hour=datetime.now().hour, notify=True))
    bot._mt5 = MT5()
    sent = []

    async def send(t):
        sent.append(t)
    bot._notifier = Notifier(enabled=True, min_interval_s=0)
    bot._notifier.attach(send)
    await bot._maybe_send_daily_report()
    await bot._maybe_send_daily_report()          # 2e passage dans la même heure → rien
    await bot._notifier.flush(); await asyncio.sleep(0.05)
    bot._notifier.stop()
    assert MT5.calls == 1 and len(sent) == 1 and sent[0].startswith("📊 Récap du")
