"""Simulation du backtest sur des prix fabriqués (pas besoin de MT5)."""
import datetime as dt

import numpy as np
import pytest

from parser.update_parser import parse_update
from tools.backtest import server_offset_h, simulate

T0 = dt.datetime(2026, 11, 25, 13, 49, 46, tzinfo=dt.timezone.utc)
SIG = dict(id=1, t=T0, dir="BUY", lo=4066.0, hi=4069.0, sl=4062.0, tps=[4072.0, None])


def ticks(path):
    """path = liste de (seconde, bid) ; ask = bid + 0.10, un tick par seconde interpolé."""
    secs = np.arange(0, path[-1][0] + 1, 1.0)
    bid = np.interp(secs, [p[0] for p in path], [p[1] for p in path])
    return secs, bid, bid + 0.10


def test_tp1_puis_be_sur_la_jambe_2():
    tk = ticks([(0, 4067), (5, 4067), (60, 4073), (120, 4066)])          # touche TP1 puis revient à l'entrée
    r = simulate(SIG, tk, n_legs=2)
    assert r["legs"][0] == pytest.approx(4072 - 4067.1)
    assert r["legs"][1] == pytest.approx(0.10, abs=0.01)        # sortie au BE (+ offset)


def test_sl_touche_avant_tp1_les_deux_jambes_perdent():
    tk = ticks([(0, 4067), (5, 4067), (30, 4061)])
    r = simulate(SIG, tk, n_legs=2)
    assert r["legs"] == [pytest.approx(4062 - 4067.1)] * 2


def test_une_seule_position():
    tk = ticks([(0, 4067), (5, 4067), (60, 4073)])
    assert len(simulate(SIG, tk, n_legs=1)["legs"]) == 1


def test_ordre_du_canal_cloturez_tp2():
    tk = ticks([(0, 4067), (5, 4067), (60, 4073), (300, 4080), (600, 4080)])
    ev = [(T0 + dt.timedelta(seconds=300), parse_update("✅ CLÔTUREZ TP2 MANUELLEMENT"))]
    r = simulate(SIG, tk, ev, n_legs=2)
    assert r["legs"][1] == pytest.approx(4080 - 4067.1)


def test_heure_serveur():
    assert server_offset_h(dt.datetime(2026, 7, 1, tzinfo=dt.timezone.utc)) == 3
    assert server_offset_h(dt.datetime(2026, 11, 25, tzinfo=dt.timezone.utc)) == 2
