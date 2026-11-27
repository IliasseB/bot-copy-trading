"""Le scanner aurait bloqué les fuites réelles vues dans ce projet."""
from pathlib import Path

from scripts.check_secrets import env_secrets, scan_files, scan_text


def test_mot_de_passe_mt5_en_clair_detecte():
    # forme exacte de l'ancienne fuite dans PROMPT_NOUVEAU_CHAT.md
    assert scan_text("PROMPT.md", "- Login : `12345678`\n- Password : `*Abcdef12`", {})


def test_jeton_github_detecte():
    assert scan_text("x", "https://KAKRI21:ghp_" + "a" * 36 + "@github.com/x.git", {})


def test_api_hash_detecte():
    assert scan_text("x", "api_hash = '0123456789abcdef0123456789abcdef'", {})


def test_valeur_du_env_detectee():
    assert scan_text("doc.md", "mon numéro +33600000000", {"TELEGRAM_PHONE": "+33600000000"})


def test_fichiers_interdits():
    problems = scan_files([".env", "trading_bot.session", "a/b.session-journal"], {}, read=lambda f: "")
    assert [p[1] for p in problems] == ["fichier interdit"] * 3


def test_placeholders_acceptes():
    assert scan_text(".env.example", "MT5_PASSWORD=your_mt5_password\nTELEGRAM_API_HASH=your_api_hash_here", {}) == []


def test_lecture_du_env(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_PHONE=+33611111111\nMT5_PASSWORD=secret123 # commentaire\nBOT_ENV=prod\n", encoding="utf-8")
    assert env_secrets(env) == {"TELEGRAM_PHONE": "+33611111111", "MT5_PASSWORD": "secret123"}


def test_le_depot_entier_ne_contient_aucun_secret():
    """Filet de sécurité : échoue si un secret (ou un fichier interdit) entre dans le dépôt."""
    from scripts.check_secrets import main
    assert main(["--all"]) == 0
