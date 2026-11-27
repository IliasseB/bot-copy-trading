"""Les secrets ne doivent jamais apparaître dans les logs."""
import logging

from utils.logger import SecretMaskingFilter


def _record(msg, *args):
    return logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)


def test_secret_masque():
    f = SecretMaskingFilter(["+33612345678", "abcdef0123456789abcdef0123456789"])
    r = _record("Connexion %s avec hash %s", "+33612345678", "abcdef0123456789abcdef0123456789")
    f.filter(r)
    assert r.getMessage() == "Connexion *** avec hash ***"


def test_message_sans_secret_inchange():
    f = SecretMaskingFilter(["motdepasse"])
    r = _record("Ordre envoyé ticket=%s", 42)
    f.filter(r)
    assert r.getMessage() == "Ordre envoyé ticket=42"


def test_secrets_trop_courts_ignores():
    f = SecretMaskingFilter(["", "ab"])
    r = _record("ab cd")
    f.filter(r)
    assert r.getMessage() == "ab cd"
