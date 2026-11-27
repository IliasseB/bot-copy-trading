"""
utils/notifier.py - Notifications sur TON Telegram ("Messages enregistrés").

Le bot t'écrit à toi-même quand il ouvre un trade, applique un ordre du canal, refuse
un signal ou rencontre une erreur. Envoi en arrière-plan : une notification lente ou
ratée ne retarde JAMAIS un ordre.
Activation : BOT_NOTIFY=true dans .env.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Optional

from utils.logger import get_logger

log = get_logger("notify")

SendFn = Callable[[str], Awaitable[None]]


class Notifier:
    def __init__(self, enabled: bool = False, min_interval_s: float = 1.0, max_queue: int = 100):
        self.enabled = enabled
        self._send: Optional[SendFn] = None
        self._queue: "asyncio.Queue[str]" = asyncio.Queue(maxsize=max_queue)
        self._min_interval = min_interval_s
        self._task: Optional[asyncio.Task] = None
        self._last = 0.0

    def attach(self, send: SendFn) -> None:
        """Branche la fonction d'envoi (le client Telegram) et démarre l'envoi en arrière-plan."""
        self._send = send
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._worker(), name="notifier")

    def notify(self, text: str) -> None:
        """Non bloquant : met le message en file. Ignoré si désactivé ou si la file déborde."""
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(text[:4000])
        except asyncio.QueueFull:
            log.warning("File de notifications pleine — message ignoré")

    async def _worker(self) -> None:
        while True:
            text = await self._queue.get()
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)       # évite le flood Telegram
            try:
                if self._send:
                    await self._send(text)
            except Exception as e:
                log.warning(f"Notification non envoyée: {e}")
            self._last = time.monotonic()

    async def flush(self, timeout: float = 5.0) -> None:
        """Attend que la file se vide (arrêt propre)."""
        end = time.monotonic() + timeout
        while not self._queue.empty() and time.monotonic() < end:
            await asyncio.sleep(0.05)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
