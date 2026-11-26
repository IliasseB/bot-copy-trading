"""
utils/instance_lock.py - Une seule instance du bot à la fois.

Deux programmes qui utilisent la même session Telegram en même temps peuvent la faire
invalider par Telegram (AuthKeyDuplicatedError → il faut refaire le code SMS).
Le verrou est un vrai verrou du système d'exploitation : il disparaît tout seul si le
bot plante ou si la fenêtre est fermée, pas de fichier "bloqué" à supprimer à la main.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

DEFAULT_LOCK = Path(__file__).resolve().parent.parent / "state" / "bot.lock"


class AlreadyRunningError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, path: Path = DEFAULT_LOCK):
        self.path = Path(path)
        self._fh: Optional[object] = None

    def acquire(self) -> "InstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise AlreadyRunningError(
                "Le bot (ou un outil qui utilise la session Telegram) tourne déjà. "
                "Ferme l'autre fenêtre avant d'en lancer une nouvelle."
            )
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return self

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None

    def __enter__(self) -> "InstanceLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()
