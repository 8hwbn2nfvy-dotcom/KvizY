"""GigaChat (Сбер). Авторизация: ключ → временный токен на 30 минут."""
from __future__ import annotations

import time
import uuid

import httpx

from app.ai.base import AIError, AIProvider, AIResult, http_error

OAUTH = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

_tokens: dict[str, tuple[str, float]] = {}


class GigaChatProvider(AIProvider):
    name = "gigachat"
    # 🟡 Примерные цены, ₽ за 1000 токенов.
    PRICES = {"GigaChat-2": (0.2, 0.2), "GigaChat-2-Pro": (1.5, 1.5), "GigaChat-2-Max": (1.95, 1.95)}
    context_tokens = 32000

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.auth_key = cfg.get("auth_key", "")
        self.scope = cfg.get("scope") or "GIGACHAT_API_PERS"
        # Сертификат Сбера подписан российским УЦ, которого нет в обычных контейнерах.
        import os
        self.verify = os.environ.get("GIGACHAT_INSECURE", "") not in ("1", "true", "yes")
        self.default_price = self.PRICES.get(self.model, (1.5, 1.5))

    def is_configured(self) -> bool:
        return bool(self.auth_key)

    def _token(self) -> str:
        tok, exp = _tokens.get(self.auth_key, ("", 0))
        if tok and time.time() < exp - 60:
            return tok
        try:
            r = httpx.post(OAUTH, data={"scope": self.scope}, verify=self.verify, timeout=30,
                           headers={"Authorization": f"Basic {self.auth_key}", "RqUID": str(uuid.uuid4()),
                                    "Content-Type": "application/x-www-form-urlencoded"})
        except httpx.HTTPError as e:
            raise AIError(f"Нет связи с GigaChat: {type(e).__name__}", "network") from e
        if r.status_code != 200:
            raise http_error("GigaChat", r.status_code, r.text)
        d = r.json()
        _tokens[self.auth_key] = (d["access_token"], d.get("expires_at", 0) / 1000 or time.time() + 1500)
        return d["access_token"]

    def chat(self, system, user, max_tokens=4000, temperature=0.3, **_):
        if not self.is_configured():
            raise AIError("Не задан ключ GigaChat. Впиши его в Настройках → ИИ.", "config")
        body = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = httpx.post(CHAT, json=body, verify=self.verify, timeout=300,
                           headers={"Authorization": f"Bearer {self._token()}"})
        except httpx.HTTPError as e:
            raise AIError(f"Нет связи с GigaChat: {type(e).__name__}", "network") from e
        if r.status_code != 200:
            raise http_error("GigaChat", r.status_code, r.text)
        d = r.json()
        u = d.get("usage", {})
        return AIResult(d["choices"][0]["message"]["content"], int(u.get("prompt_tokens", 0)),
                        int(u.get("completion_tokens", 0)))
