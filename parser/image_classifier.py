"""
parser/image_classifier.py - Reconnaît les IMAGES de gestion du canal RTZ.

Le canal poste ~1/4 de sa gestion en images sans texte, toujours les mêmes modèles :
    "TP1 HIT", "TP2 HIT", "SL HIT", "BE TOUCHÉ", "METTEZ VOUS BE !!", "+30 PIPS", "LET'S GO"...
On compare une empreinte visuelle (dHash 32×32 = 1024 bits) aux modèles connus
(parser/image_templates.json, construits sur 467 images réelles du canal) :
    même modèle      → ≤ 26 bits d'écart (recompression Telegram)
    modèles différents → ≥ 81 bits ("TP1 HIT" vs "TP2 HIT")
    image inconnue   → ≥ 445 bits
Seuil = 50 bits. Une image inconnue (nouveau design, graphique...) ne déclenche RIEN.

Pas d'OCR ni d'IA : instantané (~10 ms), gratuit, hors-ligne, et sans faux positif.
"""
from __future__ import annotations

import io
import json
import os
import re
from typing import Optional, Tuple

import numpy as np

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:          # Pillow absent → le bot continue sans lire les images
    Image = None
    PIL_AVAILABLE = False

MAX_DISTANCE = 50
TEMPLATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "image_templates.json")

# Image reconnue → texte équivalent, lu ensuite par parse_update comme un message normal
LABEL_TO_TEXT = {
    "TP1_HIT": "TP1 HIT",
    "TP2_HIT": "TP2 HIT",
    "SL_HIT":  "SL HIT",
    "BE_HIT":  "BE TOUCHÉ",
    "BE":      "METTEZ VOUS BE",
    "INFO":    None,             # "+30 PIPS", "LET'S GO" : info, aucune action
}


def label_text(label: Optional[str]) -> Optional[str]:
    """Texte équivalent d'un label ; "TP3_HIT" (nouveau modèle ajouté plus tard) → "TP3 HIT"."""
    if not label:
        return None
    if label in LABEL_TO_TEXT:
        return LABEL_TO_TEXT[label]
    m = re.fullmatch(r"TP(\d)_HIT", label)
    return f"TP{m.group(1)} HIT" if m else None


def dhash(image_bytes: bytes, n: int = 32) -> np.ndarray:
    im = Image.open(io.BytesIO(image_bytes)).convert("L").resize((n + 1, n), Image.LANCZOS)
    a = np.asarray(im, dtype=np.int16)
    return (a[:, 1:] > a[:, :-1]).flatten()


def _load_templates():
    with open(TEMPLATES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    refs = []
    for label, hashes in raw.items():
        for h in hashes:
            refs.append((label, np.unpackbits(np.frombuffer(bytes.fromhex(h), dtype=np.uint8)).astype(bool)))
    return refs


_REFS = _load_templates() if PIL_AVAILABLE and os.path.exists(TEMPLATES_PATH) else []


def classify(image_bytes: bytes) -> Tuple[Optional[str], int]:
    """Retourne (label, distance) ; label = None si l'image ne ressemble à aucun modèle connu."""
    if not PIL_AVAILABLE or not _REFS:
        return None, -1
    try:
        h = dhash(image_bytes)
    except Exception:
        return None, -1
    label, dist = min(((lab, int(np.count_nonzero(ref != h))) for lab, ref in _REFS), key=lambda x: x[1])
    return (label, dist) if dist <= MAX_DISTANCE else (None, dist)


def image_to_text(image_bytes: bytes) -> Tuple[Optional[str], Optional[str], int]:
    """(texte équivalent ou None, label, distance)."""
    label, dist = classify(image_bytes)
    return label_text(label), label, dist
