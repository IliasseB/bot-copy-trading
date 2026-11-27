from types import SimpleNamespace as NS

from utils.config_check import check_config


def cfg(**risk):
    base = dict(lot_per_tp=0.01, max_lot=0.02, fixed_lot=0.0, max_daily_loss_percent=3.0,
                trading_hours="", trading_days="")
    base.update(risk)
    return NS(risk=NS(**base), bot=NS(daily_report_hour=22, pending_expiry_minutes=30), mt5=NS(allow_real=False))


def test_config_saine():
    assert check_config(cfg()) == []


def test_max_lot_trop_petit_pour_tp2():
    # le réglage d'origine : RISK_MAX_LOT=0.01 avec 0.01 par TP → TP2 jamais pris
    msgs = check_config(cfg(max_lot=0.01))
    assert any("TP2 ne sera jamais ouverte" in m for _, m in msgs)


def test_horaires_illisibles():
    msgs = check_config(cfg(trading_hours="8h-22h"))
    assert ("ERREUR" in [lvl for lvl, _ in msgs])


def test_pas_de_stop_journalier():
    assert any("aucun arrêt" in m for _, m in check_config(cfg(max_daily_loss_percent=0)))
