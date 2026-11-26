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
