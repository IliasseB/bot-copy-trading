"""
telegram_listener/listener.py - Listener avec catch-up anti-coupure.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Callable, Coroutine, List, Optional, Set

from telethon import TelegramClient, events
from telethon.tl.types import Message, PeerChannel

from config import TelegramConfig, reveal
from parser.image_classifier import image_to_text
from utils.logger import get_logger

log = get_logger("telegram")

SignalCallback = Callable[[str, int, int, bool], Coroutine]

POLL_LIMIT        = 20   # derniers messages relus à chaque vérification
SEEN_MAX          = 2000
# Images non reconnues gardées pour pouvoir ajouter de nouveaux modèles plus tard
UNKNOWN_IMG_DIR   = os.path.join("logs", "images_inconnues")


class TelegramListener:
    def __init__(self, config: TelegramConfig, on_message: SignalCallback):
        self._cfg = config
        self._on_message = on_message
        self._group_ids: List = config.group_id_list
        self._client = TelegramClient(
            config.session_name,
            api_id=config.api_id,
            api_hash=reveal(config.api_hash),
            connection_retries=-1,
            retry_delay=1,
            auto_reconnect=True,
            receive_updates=True,
            flood_sleep_threshold=60,
        )
        self._resolved: List = []
        self._seen_ids: Set[int] = set()
        self._seen_edit_hash: dict = {}
        self._running = False
        self._poll_interval = float(getattr(config, "poll_interval_seconds", 1.0))
        self._last_poll_warn = 0.0

    async def start(self) -> None:
        log.info(f"Connexion Telegram — groupes={self._group_ids}")
        await self._client.start(phone=self._cfg.phone)
        self._resolved = await self._resolve_groups()

        @self._client.on(events.NewMessage(chats=self._resolved or None))
        async def on_new(event):
            await self._handle(event.message, is_edit=False, source="push")

        @self._client.on(events.MessageEdited(chats=self._resolved or None))
        async def on_edit(event):
            await self._handle(event.message, is_edit=True, source="push")

        self._running = True
        await self._prime_seen_ids()
        asyncio.create_task(self._poll_loop(), name="poll")

        log.info(f"✅ Bot en écoute — temps réel + vérification du canal toutes les {self._poll_interval:g}s...")
        await self._client.run_until_disconnected()

    async def stop(self) -> None:
        self._running = False
        await self._client.disconnect()

    @staticmethod
    def _delay_s(msg: Message, is_edit: bool) -> float:
        """Secondes écoulées entre l'envoi (ou la modification) du message et maintenant."""
        sent = (getattr(msg, "edit_date", None) if is_edit else None) or getattr(msg, "date", None)
        if sent is None:
            return -1.0
        return (datetime.now(timezone.utc) - sent).total_seconds()

    async def _image_text(self, msg: Message) -> Optional[str]:
        """Image sans texte → texte équivalent ("TP1 HIT", "METTEZ VOUS BE"...) ou None."""
        try:
            data = await self._client.download_media(msg, file=bytes)
        except Exception as e:
            log.warning(f"🖼️ Image msg_id={msg.id} : téléchargement impossible ({e})")
            return None
        if not data:
            return None
        text, label, dist = image_to_text(data)
        if label is None:
            log.info(f"🖼️ Image msg_id={msg.id} non reconnue (écart {dist}) — ignorée, copie dans {UNKNOWN_IMG_DIR}")
            try:
                os.makedirs(UNKNOWN_IMG_DIR, exist_ok=True)
                with open(os.path.join(UNKNOWN_IMG_DIR, f"{msg.id}.jpg"), "wb") as f:
                    f.write(data)
            except Exception:
                pass
        else:
            log.info(f"🖼️ Image msg_id={msg.id} reconnue : {label} (écart {dist})"
                     f"{' → ' + text if text else ' — info, aucune action'}")
        return text

    async def _handle(self, msg: Message, is_edit: bool, source: str = "push") -> None:
        if msg is None or msg.out:
            return
        # Image sans légende (le canal poste "TP1 HIT", "METTEZ VOUS BE !!"... en images)
        if not msg.text and getattr(msg, "photo", None) is not None:
            if is_edit or msg.id in self._seen_ids:
                return
            self._seen_ids.add(msg.id)
            self._trim_seen_ids()
            img_text = await self._image_text(msg)
            if img_text:
                reply_to = getattr(msg, "reply_to_msg_id", None)
                sent = getattr(msg, "date", None)
                age_s = (datetime.now(timezone.utc) - sent).total_seconds() if sent else 0.0
                asyncio.create_task(
                    self._dispatch(f"[IMAGE] {img_text}", msg.id, getattr(msg, "chat_id", 0) or 0,
                                   False, reply_to, age_s),
                    name=f"img_{msg.id}",
                )
            return
        if not msg.text:
            return
        text = msg.text.strip()
        if len(text) < 5:
            if not is_edit:
                self._seen_ids.add(msg.id)
            return

        new_hash = hash(text)
        if is_edit:
            if self._seen_edit_hash.get(msg.id) == new_hash:
                return
            self._seen_edit_hash[msg.id] = new_hash
        else:
            if msg.id in self._seen_ids:
                return
            self._seen_ids.add(msg.id)
            self._seen_edit_hash[msg.id] = new_hash
            self._trim_seen_ids()

        prefix = "[EDIT]" if is_edit else "[NEW] "
        delay = self._delay_s(msg, is_edit)
        log.info(
            f"{prefix} via={source} msg_id={msg.id} délai_telegram={delay:.1f}s text={text[:70]}"
        )
        reply_to = getattr(msg, "reply_to_msg_id", None)
        sent = getattr(msg, "date", None)
        age_s = (datetime.now(timezone.utc) - sent).total_seconds() if sent else 0.0
        asyncio.create_task(
            self._dispatch(text, msg.id, getattr(msg, 'chat_id', 0) or 0, is_edit, reply_to, age_s),
            name=f"{'edit' if is_edit else 'new'}_{msg.id}",
        )

    async def _dispatch(self, text, msg_id, chat_id, is_edit, reply_to=None, age_s=0.0) -> None:
        try:
            await self._on_message(text, msg_id, chat_id, is_edit, reply_to=reply_to, age_s=age_s)
        except Exception as e:
            log.error(f"Dispatch error msg_id={msg_id}: {e}")

    async def _poll_loop(self) -> None:
        """
        Filet de sécurité rapide : relit les derniers messages du canal toutes les
        `poll_interval_seconds`. Détecte les NOUVEAUX messages ET les messages MODIFIÉS
        (le trader corrige souvent son signal). Remplace l'ancien rattrapage de 20 s.
        """
        while self._running:
            await asyncio.sleep(self._poll_interval)
            for entity in self._resolved:
                try:
                    messages = await self._client.get_messages(entity, limit=POLL_LIMIT)
                except Exception as e:
                    now = time.monotonic()
                    if now - self._last_poll_warn > 30:   # évite de spammer les logs
                        self._last_poll_warn = now
                        log.warning(f"Vérification du canal échouée: {e}")
                    continue

                for msg in reversed(messages):
                    if not msg or msg.out:
                        continue
                    if not msg.text:
                        if getattr(msg, "photo", None) is not None and msg.id not in self._seen_ids:
                            await self._handle(msg, is_edit=False, source="poll")   # image de gestion
                        continue
                    if msg.id in self._seen_ids:
                        # Déjà traité : on ne regarde que si le texte a changé (= message modifié)
                        text = msg.text.strip()
                        if msg.id not in self._seen_edit_hash:
                            self._seen_edit_hash[msg.id] = hash(text)   # 1re fois : on mémorise seulement
                            continue
                        await self._handle(msg, is_edit=True, source="poll")
                    else:
                        await self._handle(msg, is_edit=False, source="poll")

    async def _prime_seen_ids(self) -> None:
        for entity in self._resolved:
            try:
                messages = await self._client.get_messages(entity, limit=POLL_LIMIT)
                for msg in messages:
                    if msg:
                        self._seen_ids.add(msg.id)
                        if msg.text:
                            self._seen_edit_hash[msg.id] = hash(msg.text.strip())
                log.info(f"Anti-doublon initialisé — {len(self._seen_ids)} IDs en mémoire")
            except Exception as e:
                log.warning(f"Initialisation anti-doublon échouée: {e}")

    def _trim_seen_ids(self) -> None:
        if len(self._seen_ids) > SEEN_MAX:
            self._seen_ids = set(sorted(self._seen_ids)[-(SEEN_MAX // 2):])
            self._seen_edit_hash = {k: v for k, v in self._seen_edit_hash.items() if k in self._seen_ids}

    async def _resolve_groups(self) -> List:
        entities = []
        for gid in self._group_ids:
            entity = await self._try_resolve(gid)
            if entity:
                entities.append(entity)
                log.info(f"✅ Groupe résolu — id={gid} nom='{getattr(entity, 'title', gid)}'")
            else:
                log.error(f"❌ Groupe non résolu: {gid}")
        return entities

    async def _try_resolve(self, gid):
        raw_id = int(gid) if isinstance(gid, str) and gid.lstrip('-').isdigit() else gid
        try:
            return await self._client.get_entity(raw_id)
        except Exception:
            pass
        if isinstance(raw_id, int) and raw_id < 0:
            abs_id = abs(raw_id)
            for attempt in [PeerChannel(abs_id), int(f"-100{abs_id}")]:
                try:
                    return await self._client.get_entity(attempt)
                except Exception:
                    pass
            try:
                async for dialog in self._client.iter_dialogs():
                    if dialog.id == raw_id or str(dialog.id).endswith(str(abs_id)):
                        return dialog.entity
            except Exception:
                pass
        return None
