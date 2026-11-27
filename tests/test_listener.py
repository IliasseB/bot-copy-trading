"""
tests/test_listener.py - Vérifie la détection rapide (nouveaux messages + modifications),
sans Telegram réel (faux client).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from telegram_listener.listener import TelegramListener


def make_msg(mid, text, edited=False):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=mid, text=text, out=False, chat_id=-100123,
        date=now - timedelta(seconds=1), edit_date=now if edited else None,
    )


class FakeClient:
    def __init__(self):
        self.history = []          # messages "du canal" (le plus récent en premier)

    async def get_messages(self, entity, limit=20):
        return list(self.history[:limit])


def make_listener(received):
    async def on_message(text, msg_id, chat_id, is_edit, reply_to=None, age_s=0.0):
        received.append((msg_id, text, is_edit))

    cfg = SimpleNamespace(
        session_name="x", api_id=1, api_hash="h" * 32, phone="+0",
        group_id_list=[1], poll_interval_seconds=0.05,
    )
    lst = TelegramListener.__new__(TelegramListener)       # sans ouvrir de vraie connexion
    lst._cfg = cfg
    lst._on_message = on_message
    lst._client = FakeClient()
    lst._resolved = [SimpleNamespace(id=1)]
    lst._seen_ids = set()
    lst._seen_edit_hash = {}
    lst._running = True
    lst._poll_interval = 0.05
    lst._last_poll_warn = 0.0
    return lst


async def run_poll(lst, seconds=0.3):
    task = asyncio.create_task(lst._poll_loop())
    await asyncio.sleep(seconds)
    lst._running = False
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.sleep(0)


async def test_new_message_detected_once():
    got = []
    lst = make_listener(got)
    lst._client.history = [make_msg(10, "🚨 BUY XAUUSD ENTRY 4055-4058 SL 4050 TP1 4062")]
    await run_poll(lst)
    assert [(m, e) for m, _, e in got] == [(10, False)]       # vu une seule fois malgré plusieurs passages


async def test_push_then_poll_no_duplicate():
    got = []
    lst = make_listener(got)
    msg = make_msg(11, "🚨 SELL XAUUSD ENTRY 4029-4035 SL 4039 TP1 4024")
    await lst._handle(msg, is_edit=False, source="push")
    lst._client.history = [msg]
    await run_poll(lst)
    await asyncio.sleep(0.05)
    assert len(got) == 1


async def test_edit_detected_by_poll():
    got = []
    lst = make_listener(got)
    lst._client.history = [make_msg(12, "🚨 BUY XAUUSD ENTRY 4055-4058 SL 4050 TP1 4062")]
    task = asyncio.create_task(lst._poll_loop())
    await asyncio.sleep(0.2)
    lst._client.history = [make_msg(12, "🚨 BUY XAUUSD ENTRY 4064-4066 SL 4059 TP1 4070", edited=True)]
    await asyncio.sleep(0.2)
    lst._running = False
    await asyncio.sleep(0.1)
    task.cancel()
    flags = [(m, e) for m, _, e in got]
    assert flags == [(12, False), (12, True)]


async def test_unchanged_old_messages_not_replayed_after_prime():
    got = []
    lst = make_listener(got)
    old = make_msg(5, "Ancien message du canal sans intérêt")
    lst._client.history = [old]
    await lst._prime_seen_ids()
    await run_poll(lst)
    assert got == []                                           # rien n'est rejoué au démarrage


async def test_floodwait_met_le_polling_en_pause():
    from telethon.errors import FloodWaitError
    received = []
    lst = make_listener(received)
    calls = []

    async def get_messages(entity, limit=20):
        calls.append(1)
        raise FloodWaitError(request=None, capture=0)
    lst._client.get_messages = get_messages
    await run_poll(lst, seconds=0.3)
    assert len(calls) == 1          # pas de rafale de requêtes pendant le blocage
