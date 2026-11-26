"""
parser/nlp_parser.py - NLP fallback parser using spaCy for messy/ambiguous signals.
Only called when regex confidence < threshold.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from .regex_parser import RegexParser, SYMBOL_ALIASES
from .signal_model import Direction, OrderType, TradingSignal

try:
    import spacy
    _NLP = spacy.load("en_core_web_sm")
    _SPACY_AVAILABLE = True
except Exception:
    _NLP = None
    _SPACY_AVAILABLE = False


class NLPParser:
    """
    NLP-assisted parser. Uses spaCy NER + rule-based post-processing.
    Falls back gracefully if spaCy is not installed.
    """

    def __init__(self):
        self._regex = RegexParser()

    def parse(
        self,
        text: str,
        msg_id: int,
        chat_id: int,
    ) -> Tuple[Optional[TradingSignal], float]:

        if not _SPACY_AVAILABLE or _NLP is None:
            return self._regex.parse(text, msg_id, chat_id)

        doc = _NLP(text)

        # Extract numbers tagged as CARDINAL or MONEY by spaCy
        numbers: List[float] = []
        for ent in doc.ents:
            if ent.label_ in ("CARDINAL", "MONEY"):
                try:
                    numbers.append(float(ent.text.replace(",", ".")))
                except ValueError:
                    pass

        # Try regex first; if it returns a signal with low confidence,
        # supplement with NLP-extracted numbers
        signal, conf = self._regex.parse(text, msg_id, chat_id)

        if signal is None:
            return None, 0.0

        # Supplement missing prices from NLP numbers
        if signal.entry is None and numbers:
            signal = signal.model_copy(update={"entry": numbers[0]})
            conf = min(conf + 0.10, 1.0)

        signal = signal.model_copy(update={
            "parser_used": "nlp",
            "confidence": conf,
            "parsed_at_ns": time.time_ns(),
        })
        return signal, conf
