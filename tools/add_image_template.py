r"""
tools/add_image_template.py - Apprendre un nouveau modèle d'image du canal.

Quand le canal change de design, le bot ignore l'image et la copie dans logs/images_inconnues/.
Regarde-la, puis :

    venv\Scripts\python.exe -m tools.add_image_template logs\images_inconnues\10920.jpg TP1_HIT

Labels : TP1_HIT, TP2_HIT, TP3_HIT..., SL_HIT, BE_HIT ("BE TOUCHÉ"), BE ("METTEZ VOUS BE"),
INFO (image sans ordre : "+30 PIPS", "LET'S GO"...).
Relance le bot ensuite.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

from parser.image_classifier import MAX_DISTANCE, TEMPLATES_PATH, classify, dhash

VALID = re.compile(r"^(TP\d_HIT|SL_HIT|BE_HIT|BE|INFO)$")


def add_template(image_path: Path, label: str, templates_path: Path = Path(TEMPLATES_PATH)) -> str:
    if not VALID.match(label):
        raise ValueError(f"Label inconnu « {label} » (attendu : TP1_HIT, SL_HIT, BE_HIT, BE, INFO...)")
    data = image_path.read_bytes()
    current, dist = classify(data)
    if current is not None and current != label and dist <= MAX_DISTANCE // 2:
        raise ValueError(f"Cette image ressemble déjà fortement à {current} (écart {dist}) — vérifie le label")
    h = np.packbits(dhash(data)).tobytes().hex()
    templates = json.loads(templates_path.read_text(encoding="utf-8"))
    hashes = templates.setdefault(label, [])
    if h in hashes:
        return f"Déjà connue comme {label}."
    hashes.append(h)
    templates_path.write_text(json.dumps(templates, indent=1), encoding="utf-8")
    return f"✅ Modèle ajouté : {image_path.name} → {label} ({len(hashes)} modèle(s) pour ce label). Relance le bot."


def main(argv) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) != 2:
        print(__doc__)
        return 1
    try:
        print(add_template(Path(argv[0]), argv[1].upper()))
        return 0
    except (OSError, ValueError) as e:
        print(f"⛔ {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
