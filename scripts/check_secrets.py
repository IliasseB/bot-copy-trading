"""
scripts/check_secrets.py - Empêche de publier un secret sur GitHub.

Bloque :
  • les fichiers interdits : .env, *.session, *.session-journal
  • toute valeur du .env local (api_hash, téléphone, mots de passe...) retrouvée dans un fichier
  • les jetons GitHub (ghp_..., github_pat_...) et un api_hash Telegram écrit en dur
  • un identifiant / mot de passe écrit en clair ("Password : `...`")

Usage :
  python scripts/check_secrets.py --staged        (hook pre-commit)
  python scripts/check_secrets.py fichier1 ...     (fichiers précis)
  python scripts/check_secrets.py --all           (tout le dépôt)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_NAMES = re.compile(r"(^|/)(\.env|.*\.session|.*\.session-journal)$")
PATTERNS = {
    "jeton GitHub": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "api_hash Telegram": re.compile(r"api_hash\s*[:=]\s*['\"`]?[0-9a-f]{32}\b", re.IGNORECASE),
    "mot de passe en clair": re.compile(r"\b(password|mot de passe|mdp)\s*:\s*`[^`\s]{4,}`", re.IGNORECASE),
}
ENV_KEYS = ("TELEGRAM_API_HASH", "TELEGRAM_API_ID", "TELEGRAM_PHONE", "MT5_PASSWORD", "MT5_LOGIN",
            "REDIS_PASSWORD", "POSTGRES_PASSWORD", "DATABASE_URL")
SKIP_SUFFIXES = (".jpg", ".png", ".pkl", ".pyc")
# Fichiers qui contiennent volontairement de FAUX secrets (tests du scanner lui-même)
ALLOWLIST = {"scripts/check_secrets.py", "tests/test_check_secrets.py"}


def env_secrets(env_path: Path = ROOT / ".env") -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.+)$", line.strip())
        if m and m.group(1) in ENV_KEYS:
            v = m.group(2).split(" #")[0].strip().strip('"').strip("'")
            if len(v) >= 5:
                out[m.group(1)] = v
    return out


def scan_text(name: str, text: str, secrets: Dict[str, str]) -> List[Tuple[str, str]]:
    found = []
    for key, value in secrets.items():
        if value in text:
            found.append((name, f"valeur de {key} (.env)"))
    for label, rx in PATTERNS.items():
        if rx.search(text):
            found.append((name, label))
    return found


def scan_files(files: Iterable[str], secrets: Dict[str, str], read=None) -> List[Tuple[str, str]]:
    problems: List[Tuple[str, str]] = []
    for f in files:
        f = f.replace("\\", "/")
        if FORBIDDEN_NAMES.search(f):
            problems.append((f, "fichier interdit"))
            continue
        if f.lower().endswith(SKIP_SUFFIXES) or f in ALLOWLIST:
            continue
        try:
            text = read(f) if read else (ROOT / f).read_text(encoding="utf-8", errors="ignore")
        except (OSError, subprocess.CalledProcessError):
            continue
        if f in ALLOWLIST:
            continue
        problems += scan_text(f, text, secrets)
    return problems


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="ignore", check=True).stdout


def main(argv: List[str]) -> int:
    for stream in (sys.stdout, sys.stderr):      # console Windows (cp1252) : pas de plantage sur ⛔
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    secrets = env_secrets()
    if "--staged" in argv:
        files = [f for f in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if f]
        problems = scan_files(files, secrets, read=lambda f: _git("show", f":{f}"))
    elif "--all" in argv:
        problems = scan_files([f for f in _git("ls-files").splitlines() if f], secrets)
    else:
        problems = scan_files([a for a in argv if not a.startswith("--")], secrets)
    if problems:
        print("⛔ SECRET DÉTECTÉ — commit bloqué :")
        for f, why in problems:
            print(f"   {f} : {why}")
        print("Retire le secret (mets-le dans .env) puis recommence.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
