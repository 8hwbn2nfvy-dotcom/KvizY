"""Вход: владелец и тестеры.

- Владелец входит только паролем (поле «логин» пустое). Пароль — переменная KVIZY_PASSWORD; если
  его сменили в Настройках, в базе лежит хэш, и он важнее переменной.
- Тестеров создаёт владелец в Настройках: логин + пароль (хранится хэшем). У каждого свои данные.
- После входа устройство получает подписанную cookie на год (HMAC, секрет SESSION_SECRET).
- В подпись входит «отпечаток» пароля и номер версии сессии: сменили пароль — все старые cookie
  этого человека перестают подходить. Выключили тестера — он сразу выходит.
- 5 ошибок за 5 минут с одного адреса — пауза.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import threading
import time
from datetime import timedelta

import sqlalchemy as sa
from fastapi import HTTPException, Request

from app import config
from app.db import OWNER_ID, LoginFailure, SessionLocal, User, new_id, now

COOKIE = "kvizy_session"
TTL = 60 * 60 * 24 * 365  # год
MAX_FAILS = 5
FAIL_WINDOW = 300
GUEST_LIMIT_RUB = 50.0  # лимит на ИИ в месяц для нового тестера (меняется в Настройках)

_cache: dict[str, tuple[float, tuple[str, int] | None]] = {}
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


def ensure_owner() -> None:
    with SessionLocal() as s:
        u = s.get(User, OWNER_ID)
        if u is None:
            s.add(User(id=OWNER_ID, session_version=1, role="owner", active=True, name="Владелец"))
            s.commit()
        elif u.role != "owner":
            u.role, u.active = "owner", True
            s.commit()


def _user_state(user_id: str) -> tuple[str, int] | None:
    """(отпечаток пароля, версия сессии) или None, если человека нет или он выключен.
    Кэш на 10 секунд, чтобы не читать базу на каждый запрос."""
    with _lock:
        hit = _cache.get(user_id)
        if hit and hit[0] > time.time():
            return hit[1]
    if user_id == OWNER_ID:
        ensure_owner()
    with SessionLocal() as s:
        u = s.get(User, user_id)
        if u is None or (u.role == "guest" and not u.active):
            state = None
        else:
            stored = u.password_hash or ""
            fp = stored or ("env:" + hashlib.sha256(config.settings.password.encode()).hexdigest())
            state = (hashlib.sha256(fp.encode()).hexdigest()[:16], u.session_version or 1)
    with _lock:
        _cache[user_id] = (time.time() + 10, state)
    return state


def forget_cache() -> None:
    with _lock:
        _cache.clear()


def _key() -> bytes:
    base = config.settings.session_secret or ("kvizy-session:" + config.settings.password)
    return hashlib.sha256(base.encode()).digest()


def _sign(payload: str, user_id: str) -> str | None:
    state = _user_state(user_id)
    if state is None:
        return None
    fp, version = state
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
    good = _sign(f"{user_id}|{exp}", user_id)
    if good is None or not hmac.compare_digest(sig, good):
        return None
    return user_id


def _owner_password_ok(pw: str) -> bool:
    with SessionLocal() as s:
        u = s.get(User, OWNER_ID)
        stored = u.password_hash if u else None
    if stored:
        return verify_hash(pw, stored)
    if not config.settings.password:
        raise HTTPException(500, "На сервере не задан KVIZY_PASSWORD. Впиши его в переменные контейнера.")
    return hmac.compare_digest(pw.encode(), config.settings.password.encode())


def norm_login(login: str) -> str:
    return re.sub(r"\s+", "", (login or "").strip().lower())


def _find_guest(login: str) -> User | None:
    with SessionLocal() as s:
        return s.scalars(sa.select(User).where(User.login == norm_login(login), User.role == "guest")).first()


def check_password(pw: str, client: str, login: str = "") -> str:
    """Возвращает user_id или бросает 401/403/429."""
    since = now() - timedelta(seconds=FAIL_WINDOW)
    with SessionLocal() as s:
        fails = s.scalar(sa.select(sa.func.count()).select_from(LoginFailure)
                         .where(LoginFailure.client == client, LoginFailure.at >= since)) or 0
    if fails >= MAX_FAILS:
        raise HTTPException(429, "Слишком много ошибок. Подожди 5 минут и попробуй снова.")
    user_id = None
    if not norm_login(login) or norm_login(login) == "owner":
        ensure_owner()
        if _owner_password_ok(pw):
            user_id = OWNER_ID
    else:
        g = _find_guest(login)
        if g and g.password_hash and verify_hash(pw, g.password_hash):
            if not g.active:
                raise HTTPException(403, "Этот аккаунт выключен. Напиши владельцу приложения.")
            user_id = g.id
    if user_id:
        with SessionLocal() as s:
            s.execute(sa.delete(LoginFailure).where(LoginFailure.client == client))
            s.commit()
        return user_id
    with SessionLocal() as s:
        s.add(LoginFailure(client=client))
        s.commit()
    left = MAX_FAILS - fails - 1
    what = "Неверный логин или пароль" if norm_login(login) else "Неверный пароль"
    raise HTTPException(401, what + (f". Осталось попыток: {left}" if left > 0 else ". Теперь пауза 5 минут."))


def change_password(old: str, new: str, user_id: str = OWNER_ID) -> None:
    with SessionLocal() as s:
        u = s.get(User, user_id)
        ok = _owner_password_ok(old) if user_id == OWNER_ID else bool(u and u.password_hash and verify_hash(old, u.password_hash))
    if not ok:
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


def is_owner(user_id: str) -> bool:
    return user_id == OWNER_ID


def require_owner(user_id: str) -> None:
    if not is_owner(user_id):
        raise HTTPException(403, "Это может только владелец приложения")


# --- Тестеры (управляет владелец) ------------------------------------------------------

def gen_password() -> str:
    """Пароль для тестера: 10 букв и цифр без похожих (0/O, 1/l)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(alphabet[b % len(alphabet)] for b in os.urandom(10))


def create_guest(name: str, login: str, password: str, limit: float | None) -> User:
    login = norm_login(login)
    if not re.fullmatch(r"[a-z0-9_.\-]{3,32}", login) or login == "owner":
        raise HTTPException(400, "Логин: 3–32 латинские буквы, цифры, точка, дефис или _")
    if len(password) < 6:
        raise HTTPException(400, "Пароль слишком короткий (нужно хотя бы 6 символов)")
    if _find_guest(login):
        raise HTTPException(400, "Такой логин уже есть")
    u = User(id=new_id(), login=login, name=(name or login).strip()[:60], role="guest", active=True,
             password_hash=hash_password(password), session_version=1,
             ai_limit_rub=GUEST_LIMIT_RUB if limit is None else max(0.0, float(limit)))
    with SessionLocal() as s:
        s.add(u)
        s.commit()
    return u


def list_guests() -> list[User]:
    with SessionLocal() as s:
        return list(s.scalars(sa.select(User).where(User.role == "guest").order_by(User.created_at)).all())


def update_guest(user_id: str, *, name=None, active=None, limit=None, password=None) -> User:
    with SessionLocal() as s:
        u = s.get(User, user_id)
        if u is None or u.role != "guest":
            raise HTTPException(404, "Нет такого тестера")
        if name is not None:
            u.name = str(name).strip()[:60] or u.login
        if active is not None:
            u.active = bool(active)
        if limit is not None:
            u.ai_limit_rub = max(0.0, float(limit))
        if password is not None:
            if len(password) < 6:
                raise HTTPException(400, "Пароль слишком короткий")
            u.password_hash = hash_password(password)
            u.session_version = (u.session_version or 1) + 1
        s.commit()
    forget_cache()
    return u
