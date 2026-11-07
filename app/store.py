"""Настройки приложения в базе (таблица settings): провайдер ИИ, модель, ключи (зашифрованы),
палитра, цены, бюджет, прошлые значения формы.

Порядок, откуда берётся значение: база → переменная окружения → значение по умолчанию.
"""
from __future__ import annotations

import os

from app import config, crypto
from app.db import OWNER_ID, SessionLocal, Setting, dumps, loads, now

# Поля провайдеров. secret — только для записи, хранятся зашифрованными.
PROVIDERS = {
    "yandex": {
        "title": "YandexGPT",
        "secret": {"api_key": "YANDEX_API_KEY"},
        "plain": {"folder_id": "YANDEX_FOLDER_ID", "model": "YANDEX_MODEL"},
        "models": [["yandexgpt-lite/latest", "YandexGPT Lite"], ["yandexgpt/latest", "YandexGPT Pro"]],
        "default_model": "yandexgpt-lite/latest",
    },
    "gigachat": {
        "title": "GigaChat",
        "secret": {"auth_key": "GIGACHAT_AUTH_KEY"},
        "plain": {"scope": "GIGACHAT_SCOPE", "model": "GIGACHAT_MODEL"},
        "models": [["GigaChat-2", "GigaChat 2 Lite"], ["GigaChat-2-Pro", "GigaChat 2 Pro"],
                   ["GigaChat-2-Max", "GigaChat 2 Max"]],
        "default_model": "GigaChat-2-Pro",
    },
    "claude": {
        "title": "Claude",
        "secret": {"api_key": "ANTHROPIC_API_KEY"},
        "plain": {"model": "ANTHROPIC_MODEL"},
        "models": [["claude-haiku-5-5", "Claude Haiku 5.5"], ["claude-sonnet-5-5", "Claude Sonnet 5.5"],
                   ["claude-opus-5-5", "Claude Opus 5.5"]],
        "default_model": "claude-opus-5-5",
    },
    "openai": {
        "title": "OpenAI-совместимый",
        "secret": {"api_key": "OPENAI_API_KEY"},
        "plain": {"base_url": "OPENAI_BASE_URL", "model": "OPENAI_MODEL"},
        "models": [],
        "default_model": "gpt-4o-mini",
    },
    "mock": {
        "title": "Пробный (без денег)",
        "secret": {},
        "plain": {"model": ""},
        "models": [["mock", "mock"]],
        "default_model": "mock",
    },
}

PALETTES = ["classic", "dark", "beige", "bluegray", "sage", "terminal", "sakura", "desert", "deep", "neon"]


def get(key: str, default=None, user_id: str = OWNER_ID):
    with SessionLocal() as s:
        row = s.get(Setting, f"{user_id}:{key}")
        return loads(row.value, default) if row else default


def put(key: str, value, user_id: str = OWNER_ID) -> None:
    with SessionLocal() as s:
        sid = f"{user_id}:{key}"
        row = s.get(Setting, sid)
        if value is None:
            if row:
                s.delete(row)
        elif row:
            row.value = dumps(value)
            row.updated_at = now()
        else:
            s.add(Setting(id=sid, user_id=user_id, key=key, value=dumps(value), updated_at=now()))
        s.commit()


def get_many(prefix: str = "", user_id: str = OWNER_ID) -> dict:
    """Все настройки разом (одним запросом к базе)."""
    import sqlalchemy as sa

    with SessionLocal() as s:
        rows = s.scalars(sa.select(Setting).where(Setting.user_id == user_id)).all()
    return {r.key: loads(r.value) for r in rows if r.key.startswith(prefix)}


# --- ИИ ------------------------------------------------------------------------------

def active_provider() -> str:
    name = (get("ai.provider") or config.settings.ai_provider or "yandex").lower()
    if name == "anthropic":
        name = "claude"
    return name if name in PROVIDERS else "yandex"


def provider_config(name: str, all_settings: dict | None = None) -> dict:
    """Готовые параметры провайдера: модель, ключи и т. п. (ключи — расшифрованные, только для сервера)."""
    st = all_settings if all_settings is not None else get_many()
    spec = PROVIDERS[name]
    cfg: dict = {}
    for field, env in spec["plain"].items():
        cfg[field] = (st.get(f"ai.{name}.{field}") or (os.environ.get(env, "").strip() if env else "") or "")
    for field, env in spec["secret"].items():
        enc = st.get(f"key.{name}.{field}")
        val = crypto.decrypt(enc) if enc else None
        cfg[field] = val or os.environ.get(env, "").strip()
        cfg[f"{field}__source"] = "db" if val else ("env" if cfg[field] else "")
        cfg[f"{field}__broken"] = bool(enc and not val)
    if not cfg.get("model"):
        cfg["model"] = spec["default_model"]
    return cfg


def public_ai_settings() -> dict:
    """То, что видит интерфейс. Ключи НИКОГДА не возвращаются — только «задан / не задан»."""
    st = get_many()
    out = {"provider": active_provider(), "providers": {}}
    for name, spec in PROVIDERS.items():
        cfg = provider_config(name, st)
        info = {
            "title": spec["title"],
            "models": spec["models"],
            "fields": {f: cfg.get(f, "") for f in spec["plain"]},
            "secrets": {f: {"set": bool(cfg.get(f)), "source": cfg.get(f"{f}__source"),
                            "broken": cfg.get(f"{f}__broken")} for f in spec["secret"]},
            "price": st.get(f"price.{name}"),
        }
        out["providers"][name] = info
    out["key_secret_set"] = bool(config.settings.key_secret)
    return out


def save_ai_settings(data: dict) -> None:
    name = data.get("provider")
    if name not in PROVIDERS:
        raise ValueError("Неизвестный провайдер")
    put("ai.provider", name)
    spec = PROVIDERS[name]
    for field in spec["plain"]:
        if field in data.get("fields", {}):
            v = str(data["fields"][field] or "").strip()
            put(f"ai.{name}.{field}", v or None)
    for field, value in (data.get("secrets") or {}).items():
        if field not in spec["secret"]:
            continue
        value = str(value or "").strip()
        if value:
            put(f"key.{name}.{field}", crypto.encrypt(value))
    if "price" in data:
        p = data["price"]
        if p and (p.get("in") not in (None, "") or p.get("out") not in (None, "")):
            put(f"price.{name}", {"in": float(p.get("in") or 0), "out": float(p.get("out") or 0)})
        else:
            put(f"price.{name}", None)


def delete_secret(name: str, field: str) -> None:
    put(f"key.{name}.{field}", None)


def budget() -> float:
    v = get("budget")
    return float(v) if v not in (None, "") else config.settings.monthly_budget
