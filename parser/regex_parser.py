"""
parser/regex_parser.py - Parser RentaTrade Z VIP.
Symbole Pepperstone: XAUUSD
"""
from __future__ import annotations

import re
import time
from typing import Dict, Optional, Tuple

from pydantic import ValidationError

from .signal_model import Direction, OrderType, TradingSignal
from utils.logger import get_logger

log = get_logger("parser")

# ── Strip Markdown Telegram (**__ __** ~~ ||) ─────────────────────────────────
_RE_MARKDOWN = re.compile(r'(\*\*|__|~~|\|\||\*|`)', re.UNICODE)

# ── Emojis ────────────────────────────────────────────────────────────────────
_RE_EMOJI = re.compile(
    "["
    "\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF\U00002700-\U000027BF\U0001F900-\U0001F9FF"
    "\u2600-\u26FF\u2700-\u27BF\U0001FA00-\U0001FA9F\U0001FAA0-\U0001FAFF"
    "]+", flags=re.UNICODE
)

_RE_BUY  = re.compile(r'\b(BUY|LONG|ACHAT)\b', re.IGNORECASE)
_RE_SELL = re.compile(r'\b(SELL|VENTE|SHORT)\b', re.IGNORECASE)
_RE_SYMBOL = re.compile(r'\b(XAUUSD|XAU\s*/?\s*USD|GOLD|OR)\b', re.IGNORECASE)

_RE_ENTRY_RANGE = re.compile(
    r'(?:ENTRY|ENTR[EÉ]E|PE|POINT\s*D.ENTR[EÉ]E)\s*:?\s*'
    r'(\d{3,6}(?:[.,]\d{1,2})?)\s*[-–]\s*(\d{3,6}(?:[.,]\d{1,2})?)',
    re.IGNORECASE
)
_RE_ENTRY_SINGLE = re.compile(
    r'(?:(?:ENTRY|ENTR[EÉ]E|PE|POINT\s*D.ENTR[EÉ]E)\s*:?|@)\s*(\d{3,6}(?:[.,]\d{1,2})?)',   # "ENTRY : 3360" ou "@3360"
    re.IGNORECASE
)
_RE_SL = re.compile(r'\bSL\s*:?\s*(\d{3,6}(?:[.,]\d{1,2})?)', re.IGNORECASE)
_RE_TP_ENTRY = re.compile(
    # "TP2: OPEN", "TP 2 : Open" (vu sur RTZ : "**__TP__**2 **__: Open__**"), "TP 4072"
    r'\bTP(?:\s*\d(?!\d))?\s*:?\s*(\d{3,6}(?:[.,]\d{1,2})?|OPEN|RUNNER|OUVERTE?)\b',
    re.IGNORECASE
)
_OPEN_WORDS = ("OPEN", "RUNNER", "OUVERT", "OUVERTE")
# "SL : A DÉFINIR" (vu 3 fois sur RTZ) → pas de stop loss chiffré
_RE_SL_UNDEFINED = re.compile(r'\bSL\s*:?\s*(?:A|À)\s*D[EÉ]FINIR', re.IGNORECASE)


def looks_like_signal(text: str) -> bool:
    """Vrai si le texte a la structure d'un signal complet (direction + ENTRY + SL)."""
    t = _RE_MARKDOWN.sub(" ", text).upper()   # "__ENTRY" : le _ empêche \b de matcher
    return bool((_RE_BUY.search(t) or _RE_SELL.search(t))
                and re.search(r'\bENTRY\b|\bENTR[EÉ]E\s*:', t) and re.search(r'\bSL\b', t))

_RE_IGNORE = re.compile(
    r'(ATTENDEZ|ATTENDS|WAIT|PATIENCE|BIENTOT|BIENTÔT|'
    r'CLIQUE\s*ICI|COMMENCER|ABONNEMENT|PREMIUM|'
    r'BONJOUR|BONSOIR|GOOD\s*MORNING|GOOD\s*NIGHT|'
    r'PIPS|SECURIS|CLOTUR|FERMER|CLOSE\s*\d|'
    r'LIVE|SESSION|YOUTUBE|DISCORD|LIEN|HTTP|WWW|'
    r'ZONE\s*UPDATE|SL\s*UPDATE|BE\s*TP|FROM\s*\d)',
    re.IGNORECASE
)

_RE_PRE_ALERT = re.compile(
    r'^[^\w]*(GOLD|XAUUSD)\s*(BUY|SELL)\s*(NOW|MAINTENANT|GO|!)*\s*$',
    re.IGNORECASE
)

# Pepperstone MT5 → XAUUSD
SYMBOL_ALIASES: Dict[str, str] = {
    "GOLD":    "XAUUSD",
    "OR":      "XAUUSD",
    "XAU/USD": "XAUUSD",
    "XAUUSD":  "XAUUSD",
    "XAUUSDM": "XAUUSD",
}


class RegexParser:

    def __init__(self, symbol_aliases: Optional[Dict[str, str]] = None):
        self._aliases = {**SYMBOL_ALIASES, **(symbol_aliases or {})}

    def parse(self, text: str, msg_id: int, chat_id: int) -> Tuple[Optional[TradingSignal], float]:
        cleaned = self._clean(text)

        if len(cleaned) < 8:
            return None, 0.0

        if _RE_PRE_ALERT.match(cleaned.strip()):
            log.debug(f"[{msg_id}] Ignoré: pré-alerte sans détails")
            return None, 0.0

        direction = None
        if _RE_BUY.search(cleaned):   direction = Direction.BUY
        elif _RE_SELL.search(cleaned): direction = Direction.SELL
        else:
            log.debug(f"[{msg_id}] Ignoré: pas de BUY/SELL")
            return None, 0.0

        sym = _RE_SYMBOL.search(cleaned)
        if not sym:
            log.debug(f"[{msg_id}] Ignoré: pas de symbole")
            return None, 0.0
        raw = sym.group(0).upper().replace(" ", "").replace("/", "")
        symbol = self._aliases.get(raw, "XAUUSD")

        has_sl    = bool(_RE_SL.search(cleaned))
        has_entry = bool(_RE_ENTRY_RANGE.search(cleaned) or _RE_ENTRY_SINGLE.search(cleaned))

        if _RE_IGNORE.search(cleaned) and not (has_entry or has_sl):
            log.debug(f"[{msg_id}] Ignoré: message de mise à jour")
            return None, 0.0

        # ENTRY
        entry_low = entry_high = entry = None
        range_m = _RE_ENTRY_RANGE.search(cleaned)
        if range_m:
            v1 = self._p(range_m.group(1))
            v2 = self._p(range_m.group(2))
            if v1 and v2:
                entry_low  = min(v1, v2)
                entry_high = max(v1, v2)
                entry = entry_low if direction == Direction.BUY else entry_high
        else:
            single = _RE_ENTRY_SINGLE.search(cleaned)
            if single:
                entry = self._p(single.group(1))
                entry_low = entry_high = entry

        # SL
        sl_m = _RE_SL.search(cleaned)
        sl   = self._p(sl_m.group(1)) if sl_m else None

        # TPs (garde OPEN/RUNNER comme jambe "sans TP fixe" = None, au lieu de l'ignorer)
        tps, seen = [], set()
        for m in _RE_TP_ENTRY.finditer(cleaned):
            raw_val = m.group(1)
            if raw_val.upper() in _OPEN_WORDS:
                tps.append(None)  # position qui reste ouverte, sans TP fixe
                continue
            v = self._p(raw_val)
            if v and v not in seen and v > 100:
                tps.append(v)
                seen.add(v)

        if sl is None and _RE_SL_UNDEFINED.search(cleaned):
            log.warning(f"[{msg_id}] Signal avec 'SL : A DÉFINIR' — ignoré (on ne trade jamais sans stop loss)")
            return None, 0.0

        confidence = self._score(direction, symbol, entry, sl, tps)

        log.info(
            f"[{msg_id}] Parser → {direction} {symbol} "
            f"zone=[{entry_low}-{entry_high}] sl={sl} tps={tps} conf={confidence:.2f}"
        )

        if confidence < 0.25:
            log.warning(f"[{msg_id}] Confiance trop faible ({confidence:.2f}) — ignoré")
            return None, 0.0

        try:
            signal = TradingSignal(
                telegram_msg_id=msg_id,
                telegram_chat_id=chat_id,
                raw_text=text,
                direction=direction,
                symbol=symbol,
                order_type=OrderType.PENDING,
                entry=entry,
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=sl,
                take_profits=tps,
                confidence=confidence,
                parser_used="regex",
                parsed_at_ns=time.time_ns(),
            )
            return signal, confidence
        except ValidationError as e:
            why = "; ".join(err.get("msg", "").replace("Value error, ", "") for err in e.errors())
            log.warning(f"[{msg_id}] ⛔ Signal incohérent ({why}) — faute de frappe probable, "
                        f"on attend que le trader corrige le message")
            return None, 0.0
        except Exception as e:
            log.error(f"[{msg_id}] Erreur création signal: {e}")
            return None, 0.0

    def _clean(self, text: str) -> str:
        text = _RE_EMOJI.sub(" ", text)
        text = _RE_MARKDOWN.sub(" ", text)
        text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
        return re.sub(r"\s{2,}", " ", text).strip().upper()

    @staticmethod
    def _p(raw: str) -> Optional[float]:
        try:
            return float(raw.replace(",", "."))
        except Exception:
            return None

    @staticmethod
    def _score(direction, symbol, entry, sl, tps) -> float:
        s = 0.0
        if direction: s += 0.25
        if symbol:    s += 0.20
        if entry:     s += 0.20
        if sl:        s += 0.20
        if tps:       s += 0.15 * min(len(tps), 2)
        return min(round(s, 3), 1.0)