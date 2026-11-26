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
