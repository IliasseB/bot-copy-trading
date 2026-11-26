"""
parser/update_parser.py - Lit les messages de GESTION d'un trade déjà ouvert (canal RTZ).

Calibré sur ~950 vrais messages de gestion du canal RTZ ACADEMY (mai → nov. 2026).
Chaque LIGNE du message est découpée en PROPOSITIONS ("METTEZ BE SUR TP2 | CLÔTUREZ 50%")
et chaque proposition donne au plus quelques commandes :

    "❌ MODIFIER SL A 4036.20"                      → SL 4036.20
    "On peut légèrement modifier le SL à 62.50"     → SL 62.50 (complété plus tard : 4062.50)
    "✅ MODIFIER TP1 A 4046" / "MODIFIER TP A 4707" → TP1 / premier TP chiffré encore ouvert
    "METTEZ BE SUR TP2" / "BE TP1 & TP2"            → BE sur ces jambes (sans n° = toutes)
    "CLÔTUREZ TP2 MANUELLEMENT" / "PRENEZ VOTRE TP1" → fermeture de CETTE jambe seulement
    "TP1 TOUCHÉ" / "TP1 ✅" / "TP1 CHECK"           → ferme la jambe TP1 si encore ouverte EN PROFIT
    "BE HIT" / "BE HIT SUR TP2"                     → le canal est sorti à BE → on sort aussi
    "SL HIT" / "SL TOUCHÉ"                          → trade fini → on ferme le reste + ordres en attente
    "SÉCURISEZ 50%" / "CLÔTUREZ 75% DES PROFITS"    → fermeture partielle
    "CLÔTUREZ LE TRADE" / "CLÔTUREZ TOUT" / "OUT"   → fermeture complète
    "SIGNAL ANNULÉ"                                 → annule les ordres en attente

Volontairement prudent — on n'agit PAS sur :
    les conditions ("SI ON REJETTE ... METTEZ-VOUS BE", "QUAND LE PRIX ATTEINDRA ...")
    les conseils ("N'HÉSITEZ PAS À CLÔTURER", "POSSIBILITÉ DE SÉCURISER PROCHE DU TP")
    les infos ("+60 PIPS", "GOLD 4126", "TP2 EN COURS", "MEILLEUR PE 40 PIPS DE SL")
    les longs textes pédagogiques (lignes > 160 caractères)
    le dispatching ("CLÔTUREZ LES POSITIONS LES PLUS HAUTES") : le bot n'a qu'une entrée.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

# Ordre d'application (fermetures d'abord, BE en dernier)
KIND_ORDER = ["CLOSE", "SL_HIT", "CANCEL", "CLOSE_LEG", "TP_HIT", "BE_HIT", "PARTIAL", "TP", "SL", "BE"]
MAX_LINE_LEN = 160


@dataclass
class UpdateCommand:
    kind: str                       # voir KIND_ORDER
    value: Optional[float] = None   # prix brut lu dans le message (peut être abrégé : 62.50)
    tp_index: Optional[int] = None  # TP : n° de jambe (1, 2...) ; None = 1er TP chiffré encore ouvert
    legs: List[int] = field(default_factory=list)  # jambes visées (1 = TP1...) ; [] = toutes ; 0 = 1re jambe chiffrée
    percent: Optional[float] = None  # PARTIAL : % du volume à fermer
    only_profit: bool = False        # CLOSE / CLOSE_LEG : ne fermer que les positions en profit
    soft: bool = field(default=False, compare=False, repr=False)  # CLOSE issu de "sécurisez vos gains"

    def __str__(self) -> str:
        legs = "+".join(f"TP{n}" if n else "TP" for n in self.legs)
        if self.kind == "TP":
            return f"TP{self.tp_index or ''}→{self.value}"
        if self.kind == "SL":
            return f"SL→{self.value}"
        if self.kind == "PARTIAL":
            return f"PARTIAL {self.percent:g}%{' ' + legs if legs else ''}"
        return f"{self.kind}{' ' + legs if legs else ''}{' (si profit)' if self.only_profit else ''}"


# ── Nettoyage ────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    t = text.replace("✅", " CHECK ").replace("☑", " CHECK ")
    t = t.replace("’", "'").replace("‘", "'")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))   # É → E, À → A, Ô → O
    t = t.upper()
    t = re.sub(r"[*_`~|]", " ", t)                     # gras / italique Telegram
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)              # 62,50 → 62.50
    t = t.replace("→", " A ").replace("->", " A ")
    t = re.sub(r"[^\w\s%+.,:;!?'/&=\-]", " ", t)       # emojis restants
    t = re.sub(r"\bBREAK[\s-]?EVEN\b|\bB\.E\b", "BE", t)
    t = re.sub(r"\bSTOP[\s-]?LOSS\b", "SL", t)
    t = re.sub(r"\bTAKE[\s-]?PROFIT\b", "TP", t)
    # Tournures qui ressemblent à une condition mais n'en sont pas pour le bot
    t = re.sub(r"\bSI VOUS (?:N'|NE )?(?:AVEZ|ETES) PAS (?:ENCORE )?(?:SECURIS|CLOTUR|MIS BE|SORTI)\w*", "POUR CEUX", t)
    t = re.sub(r"\bSI VOUS ETES ENCORE DANS LE TRADE\b", "POUR CEUX", t)
    t = re.sub(r"\bSI VOUS POUVEZ\b", "DES QUE VOUS POUVEZ", t)
    t = re.sub(r"\bZONE DE PROFIT\b", "ZONE", t)
    t = re.sub(r"\bMISE A BE\b", "METTEZ BE", t)
    # "TP1 ET TP2", "TP1 & TP2", "TP1, TP2" → "TP1 TP2" (sinon le découpage en propositions les sépare)
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"(TP\s*\d)\s*(?:ET|&|,|/|\+)\s*(?:LE\s+|LES\s+)?(?=TP\s*\d)", r"\1 ", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def _clauses(line: str) -> List[str]:
    parts = re.split(r"\s+(?:ET|AND|&|\+)\s+|[!;?/]|\.(?=\s|$)|,\s+|\s[-—–]\s", line)
    return [p.strip() for p in parts if p and p.strip()]


# ── Vocabulaire ──────────────────────────────────────────────────────────────

_NUM = r"(\d{2,5}(?:\.\d{1,2})?)"
_NOT_DISTANCE = r"(?!\d|\.\d|\s*(?:PIPS?|PTS|POINTS?|%|\$|EUR))"
_MODIF = r"(?:(?:RE)?MODIFI\w*\s*)?"

# Ligne conditionnelle / au futur / pédagogique → on ne fait rien sur cette ligne
_CONDITIONAL = re.compile(
    r"^\W*(?:CHECK\s+)?SI\b|\bSI (?:ON|LE PRIX|CA|VOUS|LE MARCHE|IL|NOUS|L'OR)\b|"
    r"\bQUAND\b|\bLORSQUE\b|\bUNE FOIS (?:QUE|LE)\b|\bLE CAS ECHEANT\b|\bPREPAR\w*|\bAVANT DE\b|"
    r"\bON ATTEND\b|\bBIENTOT\b|\bVA POUVOIR\b|\bALLEZ POUVOIR\b|\bJE \w+ERAI\b|"
    r"\bON VERRA\b|\bON SE METTRA\b|\bJE VAIS\b|\bVAIS PAS TARDER\b|"
    r"\bSOYEZ (?:A|PRETS?)\b|\bJE VOUS CONSEILLE\b|\bDES QU'ON AURA\b|\bON SECURISERA\w*|"
    r"\bJE (?:CLOTURERAI|SECURISERAI)\w*|\bJE PREFERE\b|\bJPREFERE\b|\bJ'AI PREFERE\b|"
    r"\bON VA (?:SE METTRE|SECURISER|CLOTURER)\b"
)
# Proposition = simple conseil / négation → ignorée
_ADVICE = re.compile(
    r"\bN'HESITEZ PAS\b|\bPOSSIBILITE\b|\bJ'ESPERE\b|\bPENSEZ A\b|\bPROCHE DU TP|\bPROCHE DE TP|"
    r"\bNE\s+(?:LE\s+|LES\s+|VOUS\s+)?\w+EZ\s+PAS\b|\b\w+EZ PAS\b|\bPAS ENCORE\b|"
    r"\bPAS DE (?:BE|CLOTURE|TP|RISQUE)\b|\bON PEUT DEJA\b|\bCERTAINS ON\w* PU\b|"
    r"\bAU MOMENT OPPORTUN\b|\bAPPROCHE\b|\bPUIS BE\b|\bEN FONCTION DE\b|\bSELON VO(?:TRE|S)\b|"
    r"\bSANS SE FAIRE\b|\bPRENEZ (?:QUE|SEULEMENT|JUSTE)\b|\bDE (?:SECURISER|CLOTURER|PRENDRE|FERMER)\b"
)
# Message pédagogique / bilan → ignoré en entier
_EDUCATIONAL = re.compile(
    r"\bRAPPEL\w*|\bCONSEIL\b|\bPOURQUOI\b|\bLECON\b|\bASTUCE\b|\bBILAN\b|\bPOINT IMPORTANT\b|\bIMPORTANT :"
)
# "... POUR / AFIN DE sécuriser vos gains" = but de l'action, pas un 2e ordre → on le coupe
_PURPOSE = re.compile(
    r"\b(?:AFIN DE|POUR|PERMET DE|PERMETTANT DE)\s+(?:BIEN\s+|VOUS\s+)?"
    r"(?:SECURISER|CLOTURER|PRENDRE|FERMER|EVITER|PROTEGER|ETRE)\b.*$"
)
# Dispatching (plusieurs entrées dans la zone) : le bot n'a qu'une entrée → on ne ferme rien
_DISPATCH = re.compile(r"\b(?:PLUS )?(?:HAUTES|BASSES)\b|\bPLUS (?:HAUTE|BASSE|HAUTS?|BAS)\b")

_LEG = re.compile(r"\bTP\s*([1-9])(?!\d)")
_TP_ALONE = re.compile(r"\bTP\b(?!\s*\d)")
_PERCENT = re.compile(r"(\d{1,3})\s?%")
_PARTIAL_WORD = re.compile(r"\bPARTIEL\w*|\bUNE PARTIE\b|\bMOITIE\b|\bDES PROFITS\b")
_ONLY_PROFIT = re.compile(r"\bEN PROFITS?\b|\bDES QUE VOUS POUVEZ\b")

_SETTER = re.compile(
    r"\b(?:(?:RE)?MODIFI\w*|DEPLAC\w*|BOUG\w*|METT\w*|MET|METS|PASS\w*|AJUST\w*|PLAC\w*|REMONT\w*|"
    r"DESCEND\w*|GARD\w*|CHANG\w*|NOUVEAU|NOUVEL\w*|NEW|MOVE|SET|ASSUREZ)\b"
)
_SL_VALUE = re.compile(r"\bSL\b\s*" + _MODIF + r"(?:A|AU|:|=|VERS|SUR)?\s*" + _NUM + _NOT_DISTANCE)
_TP_VALUE = re.compile(r"\bTP(?:\s*([1-9])(?!\d))?\s*" + _MODIF + r"(?:A|AU|:|=|VERS)?\s*" + _NUM + _NOT_DISTANCE)

_BE_WORD = re.compile(r"\bBE\b|\b(?:PRIX|POINT) D'ENTREE\b|\bA L'ENTREE\b")
_BE_NOT_ORDER = re.compile(
    r"\bBE\s*HIT\b|\bHIT\s*BE\b|\bTOUCH\w*\s+(?:LE\s+)?BE\b|\bBE\s+TOUCH\w*|\bFAIT\w*\s+BE\b|"
    r"\bFAIRE\s+BE\b|\bSUR LE BE\b|\bPRIS\s+(?:LE|UN)\s+BE\b|\bPRENDRE\s+(?:LE|UN)\s+BE\b|\bDE BE\b|"
    r"\bMON BE\b|\bEST BE\b|\bSOMMES BE\b|\bTERMINE\w* EN BE\b|\bTOUCH\w*\s+AU\s+BE\b"
)
_BE_START = re.compile(r"^(?:CHECK\s+)?(?:\+?\d+\s*PIPS?\s+)?(?:VOUS POUVEZ\s+|ON\s+)?(?:VOUS\s+)?BE\b")
_BE_HIT = re.compile(r"\bBE\s*HIT\b|\bHIT\s*BE\b|\bBE\s+TOUCH\w*|\bTOUCH\w*\s+(?:LE\s+)?BE\b")
_SL_HIT = re.compile(r"\bSL\s*(?:HIT|TOUCH\w*)\b|\bHIT\s*SL\b")

_TP_HIT = re.compile(
    r"\bTP\s*([1-9])?(?!\d)\s*(?:EST\s+|A ETE\s+)?(?:TOUCH\w*|HIT|CHECK\w*|ATTEINT\w*|VALID\w*|DONE|SECURED)\b|"
    r"\bINSTANT\s+TP\b"
)

# Verbes de fermeture : impératif n'importe où, infinitif seulement dans une tournure d'ordre
_CLOSE_IMPERATIVE = re.compile(
    r"\b(?:CLOTUREZ|CLOTURONS|FERMEZ|FERMONS|COUPEZ|COUPONS|SORTEZ|SORTONS|PRENEZ|PRENONS|"
    r"SECURISEZ|SECURISONS|ENCAISSEZ|SECURE|CLOSE|"
    r"JE (?:FERME|CLOTURE|SORS|SECURISE)|ON (?:SORT|FERME|CLOTURE|COUPE))\b"
)
_CLOSE_INFINITIVE = re.compile(
    r"^(?:CHECK\s+)?(?:\+?\s*\d+(?:\.\d+)?\s*(?:PIPS?)?\s+|GOLD\s+\d+(?:\.\d+)?\s+|OK\s+|MAINTENANT\s+)*"
    r"(?:CLOTURER|FERMER|COUPER|SORTIR|PRENDRE|SECURISER)\b|"
    r"\bVOUS POUVEZ (?:COMMENCER A |DONC |MAINTENANT )?(?:CLOTURER|FERMER|PRENDRE|SECURISER)\b|"
    r"\b(?:CLOTURER|FERMER|PRENDRE|SECURISER)\s+(?:VOTRE|VOS|LE|LA|LES)\s+(?:TP\s*\d|TRADE|POSITIONS?)\b|"
    r"\bCLOTURE PARTIELLE\b"
)
_CLOSE_VERB_STRICT = re.compile(r"\b(?:CLOTUR\w*|FERM\w*|COUP\w*|SORT\w*|CLOSE)\b")
_CLOSE_TARGET_ALL = re.compile(
    r"\bTRADES?\b|\bTOUT\b|\bTOUTES?\b|\bPOSITIONS?\b|\bPROFITS?\b|\bGAINS\b|\bINTEGRALEMENT\b|"
    r"\bSCALP\b|\bPERTE\b"
)
_CLOSE_STATEMENT = re.compile(
    r"\b(?:ON EST|NOUS SOMMES|ON S'EST)\s+(?:\w+\s+){0,2}SORTI\w*\s+DU\s+TRADE\b|"
    r"\b(?:NE SOMMES|N'EST|N'ETES|NE SONT)\s+PLUS\s+DANS\s+LE\s+TRADE\b|"
    r"\bEXIT\s+(?:LONG|SHORT|NOW|TRADE)\b|\bFERMEZ LE TRADE\b"
)
_OUT_START = re.compile(r"^\W*(?:CHECK\s+)?(?:INSTANT\s+)?OUT\b")
_CANCEL = re.compile(r"\bANNUL\w*|\bCANCEL\w*|\bSUPPRIMEZ\s+(?:LES|VOS)\s+ORDRES\b")


def _legs(clause: str) -> List[int]:
    return sorted({int(n) for n in _LEG.findall(clause)})


def _clause_commands(c: str) -> List[UpdateCommand]:
    cmds: List[UpdateCommand] = []
    c = _PURPOSE.sub("", c).strip()
    if not c or _ADVICE.search(c):
        return cmds
    legs = _legs(c)
    setter = bool(_SETTER.search(c))
    closing = bool(_CLOSE_IMPERATIVE.search(c) or _CLOSE_INFINITIVE.search(c))

    # ── "BE HIT" / "SL HIT" : le trade du canal est terminé ───────────────
    if _BE_HIT.search(c):
        return [UpdateCommand("BE_HIT", legs=legs)]
    if _SL_HIT.search(c):
        return [UpdateCommand("SL_HIT")]

    # ── "TP1 touché / TP1 ✅ / INSTANT TP" ─────────────────────────────────
    hit = _TP_HIT.search(c)
    hit_legs: List[int] = []
    if hit and not (setter and _TP_VALUE.search(c)) and not re.search(r"\bEN COURS\b", c):
        hit_legs = [int(hit.group(1))] if hit.group(1) else [0]
        cmds.append(UpdateCommand("TP_HIT", legs=hit_legs))
        legs = [n for n in legs if n not in hit_legs]   # "TP1 touché, mettez BE" → BE sur les AUTRES

    # ── Fermetures ────────────────────────────────────────────────────────
    if closing and not _DISPATCH.search(c):
        only_profit = bool(_ONLY_PROFIT.search(c))
        pct = _PERCENT.search(c)
        if pct or _PARTIAL_WORD.search(c):
            percent = float(pct.group(1)) if pct else 50.0
            if 0 < percent < 100:
                cmds.append(UpdateCommand("PARTIAL", legs=legs, percent=percent))
            elif percent >= 100:
                cmds.append(UpdateCommand("CLOSE_LEG", legs=legs) if legs else UpdateCommand("CLOSE"))
        elif legs:
            if not any(x.kind == "TP_HIT" for x in cmds):
                cmds.append(UpdateCommand("CLOSE_LEG", legs=legs))
        elif _TP_ALONE.search(c) and re.search(r"\b(?:PRENEZ|PRENDRE|SECURIS\w*|SECURE)\b", c):
            cmds.append(UpdateCommand("CLOSE_LEG", legs=[0], only_profit=True))  # "SÉCURISEZ TP MANUELLEMENT"
        elif not re.search(r"\bPRENEZ (?:GARDE|QUE|SEULEMENT)\b", c):
            strict = bool(_CLOSE_VERB_STRICT.search(c))
            secure_target = re.search(r"\b(?:LE TRADE|TOUT|TOUTES|POSITIONS?|PROFITS?|GAINS|SCALP)\b", c)
            if (strict and (_CLOSE_TARGET_ALL.search(c) or re.search(r"\bMANUELLEMENT\b", c))) or \
                    (not strict and secure_target):
                cmds.append(UpdateCommand("CLOSE", only_profit=only_profit, soft=not strict))
        if any(x.kind in ("CLOSE", "CLOSE_LEG", "PARTIAL") for x in cmds):
            return cmds   # "FERMEZ LE TRADE À BE" = fermer, pas modifier le SL

    # ── BE ────────────────────────────────────────────────────────────────
    if _BE_WORD.search(c) and not _BE_NOT_ORDER.search(c):
        entry_words = re.search(r"\b(?:PRIX|POINT) D'ENTREE\b|\bA L'ENTREE\b", c)
        if (setter or _BE_START.search(c)) and (not entry_words or re.search(r"\bSL\b|\bBE\b", c)):
            cmds.append(UpdateCommand("BE", legs=legs))
            return cmds

    # ── SL chiffré ────────────────────────────────────────────────────────
    m = _SL_VALUE.search(c)
    short_sl = len(c) <= 22 and re.match(r"^(?:CHECK\s+)?(?:NOUVEAU\s+)?SL\b", c)   # "SL 4060.5" seul
    if m and (setter or short_sl):
        cmds.append(UpdateCommand("SL", float(m.group(1))))

    # ── TP chiffré ────────────────────────────────────────────────────────
    if setter and not closing:
        for m in _TP_VALUE.finditer(c):
            idx = int(m.group(1)) if m.group(1) else None
            cmds.append(UpdateCommand("TP", float(m.group(2)), idx))
    return cmds


def parse_update(text: str) -> List[UpdateCommand]:
    t = _clean(text)
    cmds: List[UpdateCommand] = []
    if _EDUCATIONAL.search(t):
        return cmds
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    msg_has_modif = bool(re.search(r"\b(?:RE)?MODIFI", t))

    for line in lines:
        if len(line) > MAX_LINE_LEN:
            continue
        if _OUT_START.search(line) or _CLOSE_STATEMENT.search(line):
            cmds.append(UpdateCommand("CLOSE"))
            continue
        if _CONDITIONAL.search(line):
            continue
        if _CANCEL.search(line) and not re.search(r"\bPAS\s+ANNUL", line):
            cmds.append(UpdateCommand("CANCEL"))
        for c in _clauses(line):
            found = _clause_commands(c)
            # "🚨 MODIFICATION TP1" puis ligne "🟢 TP1 : 4255" : valeur seule sur une ligne courte
            if not found and msg_has_modif and len(c) <= 22:
                m = _TP_VALUE.search(c)
                if m:
                    found = [UpdateCommand("TP", float(m.group(2)), int(m.group(1)) if m.group(1) else None)]
            for cmd in found:
                if not any(str(cmd) == str(x) for x in cmds):
                    cmds.append(cmd)

    # "sécurisez vos gains" à côté d'un ordre précis (TP1, BE, 50 %) n'est pas une fermeture totale
    if any(x.kind in ("CLOSE_LEG", "PARTIAL", "BE", "TP_HIT") for x in cmds):
        cmds = [x for x in cmds if not (x.kind == "CLOSE" and x.soft)]
    closes = [x for x in cmds if x.kind == "CLOSE"]
    if closes:
        return [min(closes, key=lambda x: x.only_profit)]   # fermeture totale prioritaire
    return sorted(cmds, key=lambda x: KIND_ORDER.index(x.kind))


# Message SANS "répondre" : on ne garde que les ordres explicites. Les constats
# ("BE HIT", "SL touché", "TP ✅") sans réponse viennent trop souvent de témoignages.
NO_REPLY_KINDS = {"SL", "TP", "BE", "CLOSE_LEG", "PARTIAL", "CLOSE"}


def parse_update_no_reply(text: str) -> List[UpdateCommand]:
    return [c for c in parse_update(text) if c.kind in NO_REPLY_KINDS and not (c.kind == "CLOSE" and c.soft)]


def is_partial_close(text: str) -> bool:
    return any(c.kind == "PARTIAL" for c in parse_update(text))


def resolve_price(value: float, reference: float) -> float:
    """
    Les traders écrivent souvent les prix en abrégé : "SL à 62.50" pour 4062.50.
    On complète avec les chiffres du prix de référence (entrée du trade) et on
    garde la version la plus proche. Un prix complet (4062.5) est gardé tel quel.
    """
    if reference <= 0 or value >= reference * 0.5:
        return value
    step = 10 ** max(1, len(str(int(value))))   # 62.5 → 100 ; 862 → 1000
    base = math.floor(reference / step) * step
    candidates = [base - step + value, base + value, base + step + value]
    return round(min(candidates, key=lambda c: abs(c - reference)), 2)
