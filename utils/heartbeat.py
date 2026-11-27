"""
utils/heartbeat.py - Le bot écrit son état toutes les 30 s dans state/heartbeat.json.

Permet de savoir d'un coup d'œil s'il tourne encore (tools/status.py, ou un autre programme)
sans toucher à la session Telegram ni à MT5.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "state" / "heartbeat.json"
STALE_AFTER_S = 120


def write_heartbeat(info: Dict[str, Any], path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"ts": time.time(), "pid": os.getpid(), **info}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_heartbeat(path: Path = DEFAULT_PATH) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data["age_s"] = time.time() - data.get("ts", 0)
    data["alive"] = data["age_s"] < STALE_AFTER_S
    return data
