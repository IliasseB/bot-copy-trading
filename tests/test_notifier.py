import asyncio

from utils.notifier import Notifier


async def test_messages_envoyes_dans_l_ordre():
    sent = []

    async def send(t):
        sent.append(t)

    n = Notifier(enabled=True, min_interval_s=0)
    n.attach(send)
    n.notify("ouvert"); n.notify("BE")
    await n.flush(); await asyncio.sleep(0.05)
    n.stop()
    assert sent == ["ouvert", "BE"]


async def test_desactive_n_envoie_rien():
    sent = []

    async def send(t):
        sent.append(t)

    n = Notifier(enabled=False)
    n.attach(send)
    n.notify("x")
    await asyncio.sleep(0.05)
    assert sent == []


async def test_erreur_d_envoi_ne_casse_pas_la_file():
    sent = []

    async def send(t):
        if t == "boom":
            raise RuntimeError("réseau")
        sent.append(t)

    n = Notifier(enabled=True, min_interval_s=0)
    n.attach(send)
    n.notify("boom"); n.notify("ok")
    await n.flush(); await asyncio.sleep(0.05)
    n.stop()
    assert sent == ["ok"]


async def test_bot_notifie_trade_et_gestion():
    from tests.test_rtz_channel import RichFakeMT5, SIG
    from tests.test_updates import make_bot
    mt5 = RichFakeMT5(price=4068.0)
    bot = make_bot(mt5, lot=0.02)
    sent = []
    bot._notifier = Notifier(enabled=True, min_interval_s=0)

    async def send(t):
        sent.append(t)
    bot._notifier.attach(send)
    await bot._on_telegram_message(SIG, 1, 1, False)
    await bot._on_telegram_message("mettez vous BE", 2, 1, False, reply_to=1)
    await bot._notifier.flush(); await asyncio.sleep(0.05)
    bot._notifier.stop()
    assert sent[0].startswith("🎯 BUY XAUUSD ouvert à 4068.0")
    assert "BE" in sent[1] and "signal #1" in sent[1]
