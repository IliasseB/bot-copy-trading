"""
parser/signal_parser.py - Orchestrateur avec logs complets.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Set, Tuple

from utils.logger import get_logger

from .nlp_parser import NLPParser
from .regex_parser import RegexParser
from .signal_model import TradingSignal

log = get_logger("parser")

NLP_FALLBACK_THRESHOLD = 0.6
MIN_CONFIDENCE = 0.45


def _fingerprint(text: str, chat_id: int) -> str:
    key = f"{chat_id}:{text.strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


class SignalParser:
    def __init__(self, dedup_cache=None):
        self._regex = RegexParser()
        self._nlp   = NLPParser()
        self._dedup_cache = dedup_cache
        self._local_dedup: Set[str] = set()

    async def parse(
        self, text: str, msg_id: int, chat_id: int
    ) -> Tuple[Optional[TradingSignal], float]:

        fp = _fingerprint(text, chat_id)
        if await self._is_duplicate(fp):
            log.info(f"[{msg_id}] Doublon ignoré")
            return None, 0.0
        await self._register(fp)

        # Stage 1: Regex
        signal, conf = self._regex.parse(text, msg_id, chat_id)

        if signal is None:
            return None, 0.0

        # Stage 2: NLP fallback si confiance faible
        if conf < NLP_FALLBACK_THRESHOLD:
            nlp_signal, nlp_conf = self._nlp.parse(text, msg_id, chat_id)
            if nlp_signal and nlp_conf > conf:
                signal, conf = nlp_signal, nlp_conf
                log.info(f"[{msg_id}] NLP a amélioré la confiance: {conf:.2f}")

        if conf < MIN_CONFIDENCE:
            log.warning(f"[{msg_id}] Confiance finale {conf:.2f} < seuil {MIN_CONFIDENCE} — ignoré")
            return None, conf

        log.info(f"[{msg_id}] ✅ Signal accepté conf={conf:.2f}")
        return signal, conf

    async def _is_duplicate(self, fp: str) -> bool:
        if fp in self._local_dedup:
            return True
        if self._dedup_cache:
            try:
                return bool(await self._dedup_cache.exists(f"dedup:{fp}"))
            except Exception:
                pass
        return False

    async def _register(self, fp: str, ttl: int = 120) -> None:
        self._local_dedup.add(fp)
        if len(self._local_dedup) > 1000:
            self._local_dedup.clear()
        if self._dedup_cache:
            try:
                await self._dedup_cache.setex(f"dedup:{fp}", ttl, "1")
            except Exception:
                pass
