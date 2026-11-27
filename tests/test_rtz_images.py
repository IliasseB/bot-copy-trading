"""
Images de gestion du canal RTZ (vraies images, réduites à 400 px) : reconnaissance,
traduction en ordre, et passage par le listener + le bot (MT5 simulé).
"""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from parser.image_classifier import classify, image_to_text
from parser.update_parser import parse_update
from tests.test_listener import make_listener, run_poll
from tests.test_rtz_channel import RichFakeMT5, SIG
from tests.test_updates import make_bot

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "rtz_images")


def img(name):
    with open(os.path.join(FIX, f"{name}.jpg"), "rb") as f:
        return f.read()


@pytest.mark.parametrize("name, label, orders", [
    ("TP1_HIT", "TP1_HIT", ["TP_HIT TP1"]),
    ("TP1_HIT_old", "TP1_HIT", ["TP_HIT TP1"]),
    ("TP2_HIT", "TP2_HIT", ["TP_HIT TP2"]),
    ("SL_HIT", "SL_HIT", ["SL_HIT"]),
    ("BE_HIT", "BE_HIT", ["BE_HIT"]),
    ("BE", "BE", ["BE"]),
    ("BE_old", "BE", ["BE"]),
])
def test_images_reconnues(name, label, orders):
    text, lab, dist = image_to_text(img(name))
    assert lab == label and 0 <= dist <= 50
    assert [str(c) for c in parse_update(f"[IMAGE] {text}")] == orders


def test_image_info_sans_action():
    text, lab, _ = image_to_text(img("INFO"))           # "+30 PIPS"
    assert lab == "INFO" and text is None


def test_image_inconnue_ignoree():
    assert classify(img("UNKNOWN"))[0] is None           # capture d'écran de positions
    assert classify(b"pas une image")[0] is None


def _photo_msg(mid, reply_to=None):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(id=mid, text="", out=False, chat_id=-100123, photo=object(),
                           reply_to_msg_id=reply_to, date=now - timedelta(seconds=1), edit_date=None)


async def test_listener_transforme_image_en_ordre():
    received = []
    lst = make_listener(received)

    async def download_media(msg, file=None):
        return img("BE")
    lst._client.download_media = download_media
    lst._client.history = [_photo_msg(501, reply_to=500)]
    await run_poll(lst)
    assert received == [(501, "[IMAGE] METTEZ VOUS BE", False)]


async def test_listener_image_info_non_transmise():
    received = []
    lst = make_listener(received)

    async def download_media(msg, file=None):
        return img("INFO")
    lst._client.download_media = download_media
    lst._client.history = [_photo_msg(601, reply_to=600)]
    await run_poll(lst)
    assert received == []


async def test_image_mettez_vous_be_met_les_positions_au_be():
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 700, 1, False)
    await bot._on_telegram_message("[IMAGE] METTEZ VOUS BE", 701, 1, False, reply_to=700)
    assert all(p["sl"] == 4068.0 for p in mt5.positions.values())


async def test_image_sl_hit_annule_les_ordres_en_attente():
    mt5 = RichFakeMT5(price=4075.0)
    bot = make_bot(mt5, lot=0.02)
    await bot._on_telegram_message(SIG, 800, 1, False)
    assert len(mt5.orders) == 2
    await bot._on_telegram_message("[IMAGE] SL HIT", 801, 1, False, reply_to=800)
    assert mt5.orders == {}
