import json
import shutil
from pathlib import Path

import pytest

from parser.image_classifier import TEMPLATES_PATH
from tools.add_image_template import add_template

FIX = Path(__file__).parent / "fixtures" / "rtz_images"


@pytest.fixture
def templates(tmp_path):
    p = tmp_path / "templates.json"
    shutil.copy(TEMPLATES_PATH, p)
    return p


def test_ajout_d_un_nouveau_modele(templates):
    msg = add_template(FIX / "UNKNOWN.jpg", "INFO", templates)
    assert msg.startswith("✅")
    assert len(json.loads(templates.read_text())["INFO"]) == 17


def test_label_invalide(templates):
    with pytest.raises(ValueError):
        add_template(FIX / "UNKNOWN.jpg", "ACHETE", templates)


def test_refuse_un_label_contradictoire(templates):
    with pytest.raises(ValueError):
        add_template(FIX / "TP1_HIT_old.jpg", "SL_HIT", templates)   # c'est un TP1 HIT évident
