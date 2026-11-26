"""
utils/state_store.py - Petite mémoire du bot sur disque (state/bot_state.json).

Garde ce qui doit survivre à un redémarrage : le fil des réponses (message → signal
d'origine) et le dernier signal vu. Écriture atomique : un arrêt brutal ne corrompt jamais le fichier.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "state" / "bot_state.json"


class StateStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)

    def load(self) -> Dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.path)
