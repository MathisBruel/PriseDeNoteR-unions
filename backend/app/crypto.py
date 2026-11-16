import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_config

PREFIX = "enc:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_config().app_secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    if not value:
        return ""
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value or ""
    try:
        return _fernet().decrypt(value[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        # APP_SECRET_KEY a changé : le secret est perdu, il faudra le ressaisir.
        return ""
