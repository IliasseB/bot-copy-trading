r"""
tools/export_channel.py - Exporte l'historique du canal en JSON (LECTURE SEULE).

    venv\Scripts\python.exe -m tools.export_channel --limit 4000 --out state/channel.json
    venv\Scripts\python.exe -m tools.export_channel --images state/images   (+ images sans texte)

Utilise la session Telegram du bot : le bot doit être ARRÊTÉ (verrou d'instance),
sinon Telegram peut invalider la session. Aucun message n'est envoyé.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import PeerChannel

from config import get_config, reveal
from utils.instance_lock import AlreadyRunningError, InstanceLock


async def _resolve(client, gid):
    for cand in (gid, PeerChannel(abs(gid)), int(f"-100{abs(gid)}")):
        try:
            return await client.get_entity(cand)
        except Exception:
            pass
    async for d in client.iter_dialogs():
        if d.id == gid or str(d.id).endswith(str(abs(gid))):
            return d.entity
    return None


async def export(limit: int, out: Path, images: Path = None) -> int:
    tg = get_config().telegram
    client = TelegramClient(tg.session_name, tg.api_id, reveal(tg.api_hash), receive_updates=False)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("Session non autorisée : lance d'abord le bot une fois pour te connecter.")
            return 1
        ent = await _resolve(client, int(str(tg.group_id_list[0])))
        if ent is None:
            print("Canal introuvable.")
            return 1
        rows = []
        async for m in client.iter_messages(ent, limit=limit):
            rows.append({"id": m.id, "date": m.date.isoformat() if m.date else None,
                         "edit_date": m.edit_date.isoformat() if m.edit_date else None,
                         "reply_to": m.reply_to_msg_id, "text": m.text or "", "raw": m.raw_text or "",
                         "media": type(m.media).__name__ if m.media else None})
            if images and m.photo and not (m.text or "").strip():
                images.mkdir(parents=True, exist_ok=True)
                await client.download_media(m, file=str(images / f"{m.id}_r{m.reply_to_msg_id}.jpg"))
        rows.reverse()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(rows)} messages de « {getattr(ent, 'title', ent)} » → {out}")
        return 0
    finally:
        await client.disconnect()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Export de l'historique du canal (lecture seule)")
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--out", type=Path, default=Path("state") / "channel.json")
    ap.add_argument("--images", type=Path, default=None, help="dossier où enregistrer les images sans texte")
    a = ap.parse_args()
    try:
        with InstanceLock():
            return asyncio.run(export(a.limit, a.out, a.images))
    except AlreadyRunningError:
        print("⛔ Le bot tourne : arrête-le d'abord (même session Telegram).")
        return 2


if __name__ == "__main__":
    sys.exit(main())
