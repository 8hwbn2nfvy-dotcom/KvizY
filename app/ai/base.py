"""Общий интерфейс ИИ-провайдера («адаптер»).

Любой провайдер умеет одно: chat(system, user, max_tokens) → (текст, токены_вход, токены_выход).
Всё остальное (промпты, разбор JSON, учёт денег) — общее, в app/ai/__init__.py.
"""
from __future__ import annotations

from dataclasses import dataclass


class AIError(RuntimeError):
    """Ошибка ИИ с понятным текстом для человека.

    kind: auth (ключ не подошёл) | money (нет денег / нет платёжного аккаунта) | limit (лимит запросов)
          | network (нет связи) | config (не заданы настройки) | format (ответ не JSON) | other
    """

    def __init__(self, message: str, kind: str = "other"):
        super().__init__(message)
        self.kind = kind


@dataclass
class AIResult:
    text: str
    tokens_in: int
    tokens_out: int


class AIProvider:
    name = "base"
    # Цена по умолчанию, ₽ за 1000 токенов (ввод, вывод). Можно переопределить в настройках.
    default_price = (0.0, 0.0)
    # Сколько токенов помещается в один запрос (запрос + ответ вместе).
    context_tokens = 32000
    # Сколько токенов максимум можно попросить в ответе.
    max_output = 8000

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.model = cfg.get("model") or ""

    def is_configured(self) -> bool:
        return True

    def chat(self, system: str, user: str, max_tokens: int = 4000, temperature: float = 0.3, **extra) -> AIResult:
        raise NotImplementedError


def http_error(provider: str, status: int, text: str) -> AIError:
    """Превращаем код ответа сервиса в понятное сообщение."""
    low = (text or "").lower()
    short = (text or "")[:300]
    if any(w in low for w in ("billing", "payment", "balance", "insufficient", "credit", "оплат", "баланс", "quota exceeded for")):
        return AIError(f"{provider}: нет денег на счёте или не подключён платёжный аккаунт. ({status}) {short}", "money")
    if status in (401, 403):
        return AIError(f"{provider}: ключ не подошёл или у него нет прав ({status}). {short}", "auth")
    if status == 402:
        return AIError(f"{provider}: нет денег на счёте ({status}).", "money")
    if status == 429:
        return AIError(f"{provider}: превышен лимит запросов (например, бесплатные запросы на этот час). "
                       f"Подожди немного и нажми «Повторить».", "limit")
    if status >= 500:
        return AIError(f"{provider}: сервис временно не отвечает ({status}). Нажми «Повторить» чуть позже.", "network")
    return AIError(f"{provider} ответил {status}: {short}")
