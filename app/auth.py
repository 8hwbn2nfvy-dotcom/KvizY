"""Вход по одному паролю.

- Пароль — переменная KVIZY_PASSWORD. Если пароль сменили в настройках приложения, в базе лежит
  его хэш, и он важнее переменной.
- После входа устройство получает подписанную cookie на год (HMAC, секрет SESSION_SECRET).
- В подпись входит «отпечаток» текущего пароля и номер версии сессии: стоит сменить пароль —
  все старые cookie перестают подходить, и все устройства выходят.
- 5 ошибок за 5 минут с одного адреса — пауза.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import time
from datetime import timedelta

import sqlalchemy as sa
from fastapi import HTTPException, Request

from app import config
from app.db import OWNER_ID, LoginFailure, SessionLocal, User, now

COOKIE = "kvizy_session"
TTL = 60 * 60 * 24 * 365  # год
MAX_FAILS = 5
FAIL_WINDOW = 300

_cache: dict[str, tuple[float, tuple[str, int]]] = {}
_lock = threading.Lock()


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return "scrypt$" + salt.hex() + "$" + h.hex()


def verify_hash(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def _user_state(user_id: str = OWNER_ID) -> tuple[str, int]:
    """(отпечаток пароля, версия сессии). Кэш на 10 секунд, чтобы не читать базу на каждый запрос."""
    with _lock:
        hit = _cache.get(user_id)
        if hit and hit[0] > time.time():
            return hit[1]
    with SessionLocal() as s:
        u = s.get(User, user_id)
        if u is None:
            u = User(id=user_id, session_version=1)
            s.add(u)
            s.commit()
        stored = u.password_hash or ""
        version = u.session_version or 1
    fp = stored or ("env:" + hashlib.sha256(config.settings.password.encode()).hexdigest())
    state = (hashlib.sha256(fp.encode()).hexdigest()[:16], version)
    with _lock:
        _cache[user_id] = (time.time() + 10, state)
    return state


def forget_cache() -> None:
    with _lock:
        _cache.clear()


def _key() -> bytes:
    base = config.settings.session_secret or ("kvizy-session:" + config.settings.password)
    return hashlib.sha256(base.encode()).digest()


def _sign(payload: str, user_id: str) -> str:
    fp, version = _user_state(user_id)
    return hmac.new(_key(), f"{payload}|{fp}|{version}".encode(), hashlib.sha256).hexdigest()


def make_token(user_id: str = OWNER_ID) -> str:
    payload = f"{user_id}|{int(time.time()) + TTL}"
    raw = f"{payload}|{_sign(payload, user_id)}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def read_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        user_id, exp, sig = raw.rsplit("|", 2)
    except Exception:
        return None
    if int(exp) < time.time():
        return None
    if not hmac.compare_digest(sig, _sign(f"{user_id}|{exp}", user_id)):
        return None
    return user_id


def _password_ok(pw: str, user_id: str = OWNER_ID) -> bool:
    with SessionLocal() as s:
        u = s.get(User, user_id)
        stored = u.password_hash if u else None
    if stored:
        return verify_hash(pw, stored)
    if not config.settings.password:
        raise HTTPException(500, "На сервере не задан KVIZY_PASSWORD. Впиши его в переменные контейнера.")
    return hmac.compare_digest(pw.encode(), config.settings.password.encode())


def check_password(pw: str, client: str) -> str:
    """Возвращает user_id или бросает 401/429."""
    since = now() - timedelta(seconds=FAIL_WINDOW)
    with SessionLocal() as s:
        fails = s.scalar(sa.select(sa.func.count()).select_from(LoginFailure)
                         .where(LoginFailure.client == client, LoginFailure.at >= since)) or 0
    if fails >= MAX_FAILS:
        raise HTTPException(429, "Слишком много ошибок. Подожди 5 минут и попробуй снова.")
    if _password_ok(pw):
        with SessionLocal() as s:
            s.execute(sa.delete(LoginFailure).where(LoginFailure.client == client))
            s.commit()
        return OWNER_ID
    with SessionLocal() as s:
        s.add(LoginFailure(client=client))
        s.commit()
    left = MAX_FAILS - fails - 1
    raise HTTPException(401, "Неверный пароль" + (f". Осталось попыток: {left}" if left > 0 else ". Теперь пауза 5 минут."))


def change_password(old: str, new: str, user_id: str = OWNER_ID) -> None:
    if not _password_ok(old, user_id):
        raise HTTPException(400, "Старый пароль не подошёл")
    if len(new) < 6:
        raise HTTPException(400, "Новый пароль слишком короткий (нужно хотя бы 6 символов)")
    with SessionLocal() as s:
        u = s.get(User, user_id)
        u.password_hash = hash_password(new)
        u.session_version = (u.session_version or 1) + 1
        s.commit()
    forget_cache()


def client_id(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() or (request.client.host if request.client else "?"))[:64]


def current_user(request: Request) -> str:
    user_id = read_token(request.cookies.get(COOKIE))
    if not user_id:
        raise HTTPException(401, "Нужно войти")
    return user_id
