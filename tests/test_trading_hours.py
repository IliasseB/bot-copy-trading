from datetime import datetime

import pytest

from utils.trading_hours import is_trading_time, parse_days

MERCREDI_10H = datetime(2026, 11, 25, 10, 0)
MERCREDI_23H = datetime(2026, 11, 25, 23, 0)
SAMEDI_10H = datetime(2026, 11, 28, 10, 0)


def test_sans_restriction():
    assert is_trading_time("", "", MERCREDI_23H)


@pytest.mark.parametrize("hours, now, ok", [
    ("08:00-22:00", MERCREDI_10H, True),
    ("08:00-22:00", MERCREDI_23H, False),
    ("08:00-09:00,09:30-12:00", MERCREDI_10H, True),
    ("22:00-02:00", MERCREDI_23H, True),          # passe minuit
    ("22:00-02:00", MERCREDI_10H, False),
])
def test_plages(hours, now, ok):
    assert is_trading_time(hours, "", now) is ok


def test_jours():
    assert parse_days("1-5") == {1, 2, 3, 4, 5}
    assert is_trading_time("", "1-5", MERCREDI_10H)
    assert not is_trading_time("", "1-5", SAMEDI_10H)
