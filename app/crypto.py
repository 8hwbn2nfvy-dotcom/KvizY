"""Шифрование ключей ИИ в базе. Секрет — переменная окружения KEY_SECRET.

Без KEY_SECRET ключи в базу не сохраняются (берутся только из переменных окружения).
Если KEY_SECRET поменять, старые сохранённые ключи перестанут расшифровываться —
их нужно будет ввести в настройках заново.
"""
from __future__ import annotations

import base64
import hashlib

from app import config


class KeySecretMissing(RuntimeError):
    pass


def _fernet():
    from cryptography.fernet import Fernet

    secret = config.settings.key_secret
    if not secret:
        raise KeySecretMissing("На сервере не задан KEY_SECRET — ключ негде надёжно сохранить.")
    key = base64.urlsafe_b64encode(hashlib.sha256(("kvizy-keys:" + secret).encode()).digest())
    return Fernet(key)


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str | None:
    """None — если расшифровать не получилось (сменили KEY_SECRET)."""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, KeySecretMissing, ValueError):
        return None
