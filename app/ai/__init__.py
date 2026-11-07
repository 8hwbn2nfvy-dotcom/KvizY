"""ИИ-адаптер. Какой провайдер работает — решает настройка в приложении (Настройки → ИИ),
а если там пусто — переменная AI_PROVIDER. Код менять не нужно.

Главное здесь — ask_json(): запрос → терпимый разбор JSON → при неудаче ещё одна попытка →
запись расхода в таблицу usage.
"""
from __future__ import annotations

import json
import re

from app import store
from app.ai.base import AIError, AIProvider, AIResult

PROVIDERS = {
    "yandex": "app.ai.yandex:YandexProvider",
    "gigachat": "app.ai.gigachat:GigaChatProvider",
    "claude": "app.ai.claude:ClaudeProvider",
    "openai": "app.ai.openai_compat:OpenAICompatProvider",
    "mock": "app.ai.mock:MockProvider",
}

_override: AIProvider | None = None


def use_provider(provider: AIProvider | None) -> None:
    """Подменить провайдера (для тестов)."""
    global _override
    _override = provider


def get_provider(name: str | None = None) -> AIProvider:
    if _override is not None and name is None:
        return _override
    name = name or store.active_provider()
    if name not in PROVIDERS:
        raise AIError(f"Неизвестный провайдер ИИ: {name}", "config")
    mod, cls = PROVIDERS[name].split(":")
    module = __import__(mod, fromlist=[cls])
    return getattr(module, cls)(store.provider_config(name))


def prices(p: AIProvider) -> tuple[float, float]:
    custom = store.get(f"price.{p.name}")
    if custom:
        return float(custom.get("in") or 0), float(custom.get("out") or 0)
    return p.default_price


def price_of(p: AIProvider, tokens_in: int, tokens_out: int) -> float:
    pin, pout = prices(p)
    return round(tokens_in / 1000 * pin + tokens_out / 1000 * pout, 4)


# --- Терпимый разбор JSON ------------------------------------------------------------

WRAPPERS = ("quiz", "result", "data", "response", "answer", "output", "квиз", "ответ")


def parse_json(text: str):
    """Достаём JSON из ответа ИИ, даже если он в ```json ... ```, с текстом вокруг
    или с лишними запятыми в конце списков."""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    candidates = [t]
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s, e = t.find(open_c), t.rfind(close_c)
        if s >= 0 and e > s:
            candidates.append(t[s:e + 1])
    for c in candidates:
        for variant in (c, re.sub(r",\s*([}\]])", r"\1", c)):
            try:
                return json.loads(variant)
            except (json.JSONDecodeError, ValueError):
                continue
    raise ValueError("ответ не похож на JSON")


def unwrap(data, expect: tuple[str, ...] = ()):
    """Разворачиваем обёртки вида {"quiz": {...}} → {...}, если нужных полей сверху нет."""
    for _ in range(3):
        if not isinstance(data, dict) or any(k in data for k in expect):
            return data
        inner = None
        for k in WRAPPERS:
            if isinstance(data.get(k), (dict, list)):
                inner = data[k]
                break
        if inner is None and len(data) == 1:
            only = next(iter(data.values()))
            if isinstance(only, (dict, list)):
                inner = only
        if inner is None:
            return data
        data = inner
    return data


def to_number(v, default: float = 0.0) -> float:
    """Число, даже если ИИ прислал строкой: "1", "0,5", "0.5 балла"."""
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:[.,]\d+)?", v)
        if m:
            return float(m.group(0).replace(",", "."))
    return default


# --- Главный вызов -------------------------------------------------------------------

SYSTEM_RULES = (
    "Ты помогаешь человеку учиться по его материалам. Правила:\n"
    "1. Вопросы проверяют понимание: причины, связи, применение, сравнение — а не только поиск слова в тексте.\n"
    "2. Пиши только по-русски.\n"
    "3. Не выдумывай факты. Всё, что утверждаешь, должно следовать из материала "
    "(кроме вопросов с пометкой «вне файла» — там можно общеизвестное, близкое к теме).\n"
    "4. Поле quote — ДОСЛОВНЫЙ кусок из материала (скопируй без изменений, 5–30 слов).\n"
    "5. Отвечай ТОЛЬКО корректным JSON, без пояснений и без ```."
)


def log_usage(p: AIProvider, purpose: str, tokens_in: int, tokens_out: int,
              quiz_id: str | None, quiz_title: str | None, user_id: str) -> float:
    from app.db import SessionLocal, Usage

    cost = price_of(p, tokens_in, tokens_out)
    with SessionLocal() as s:
        s.add(Usage(user_id=user_id, quiz_id=quiz_id, quiz_title=quiz_title, provider=p.name, model=p.model,
                    purpose=purpose, tokens_in=tokens_in, tokens_out=tokens_out, cost_rub=cost))
        s.commit()
    return cost


def month_spent(user_id: str) -> float:
    """Сколько человек потратил на ИИ в этом месяце, ₽."""
    import datetime as dt

    import sqlalchemy as sa

    from app.db import SessionLocal, Usage, now
    n = now()
    start = dt.datetime(n.year, n.month, 1)
    with SessionLocal() as s:
        v = s.scalar(sa.select(sa.func.sum(Usage.cost_rub)).where(Usage.user_id == user_id, Usage.created_at >= start))
    return float(v or 0)


def check_quota(user_id: str) -> None:
    """У тестеров есть лимит на ИИ в месяц. Исчерпан — новые запросы к ИИ не идут (у владельца лимита нет)."""
    from app.db import OWNER_ID, SessionLocal, User
    if user_id == OWNER_ID:
        return
    with SessionLocal() as s:
        u = s.get(User, user_id)
    if u is None or u.ai_limit_rub is None:
        return
    spent = month_spent(user_id)
    if spent >= u.ai_limit_rub:
        raise AIError(f"Лимит на ИИ в этом месяце исчерпан ({spent:.0f} из {u.ai_limit_rub:.0f} ₽). "
                      f"Попроси владельца приложения поднять лимит. С 1-го числа лимит обновится.", "quota")


def ask_json(purpose: str, user: str, *, system_extra: str = "", expect: tuple[str, ...] = (),
             quiz_id: str | None = None, quiz_title: str | None = None, user_id: str = "owner",
             max_tokens: int = 4000, mock=None, provider: AIProvider | None = None):
    """Спросить ИИ и получить JSON. Каждый вызов (даже неудачный по формату) записывается в расходы."""
    check_quota(user_id)
    p = provider or get_provider()
    system = SYSTEM_RULES + ("\n" + system_extra if system_extra else "")
    total_in = total_out = 0
    data = None
    prompt = user
    try:
        for _ in range(2):
            res: AIResult = p.chat(system, prompt, max_tokens=max_tokens, purpose=purpose, mock=mock)
            total_in += res.tokens_in
            total_out += res.tokens_out
            try:
                data = unwrap(parse_json(res.text), expect)
                break
            except ValueError:
                prompt = user + "\n\nПрошлый ответ был не JSON. Верни ТОЛЬКО корректный JSON без пояснений."
    finally:
        if total_in or total_out:
            log_usage(p, purpose, total_in, total_out, quiz_id, quiz_title, user_id)
    if data is None:
        raise AIError("ИИ дважды ответил не в формате JSON. Нажми «Повторить».", "format")
    return data


def check_connection(name: str) -> dict:
    """Маленький пробный запрос для кнопки «Проверить связь»."""
    try:
        p = get_provider(name)
        res = p.chat("Ответь только JSON.", 'Верни {"ok": true}. ping', max_tokens=20, purpose="ping")
        log_usage(p, "ping", res.tokens_in, res.tokens_out, None, None, "owner")
        return {"ok": True, "message": f"Работает ✓ ({p.model})"}
    except AIError as e:
        hints = {
            "auth": "Ключ не подошёл. Проверь, что скопировал его целиком и что у ключа есть нужная роль.",
            "money": "Нет денег на счёте или не подключён платёжный аккаунт.",
            "limit": "Лимит запросов. Подожди немного и проверь ещё раз.",
            "network": "Нет связи с сервисом. Попробуй ещё раз чуть позже.",
            "config": "Не хватает настроек.",
        }
        return {"ok": False, "kind": e.kind, "message": hints.get(e.kind, "Не работает."), "details": str(e)}
    except Exception as e:  # неожиданное — тоже показываем
        return {"ok": False, "kind": "other", "message": "Не работает.", "details": f"{type(e).__name__}: {e}"}
