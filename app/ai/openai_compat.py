"""Любой сервис с OpenAI-совместимым API (OpenAI, DeepSeek, OpenRouter и т. п.): адрес + ключ + модель."""
from __future__ import annotations

import httpx

from app.ai.base import AIError, AIProvider, AIResult, http_error


class OpenAICompatProvider(AIProvider):
    name = "openai"
    default_price = (0.1, 0.4)  # 🟡 впиши реальную цену в настройках
    context_tokens = 64000
    max_output = 8000

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.key = cfg.get("api_key", "")
        self.base = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")

    def is_configured(self) -> bool:
        return bool(self.key)

    def chat(self, system, user, max_tokens=4000, temperature=0.3, **_):
        if not self.is_configured():
            raise AIError("Не задан ключ OpenAI-совместимого сервиса. Впиши его в Настройках → ИИ.", "config")
        body = {"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = httpx.post(f"{self.base}/chat/completions", json=body, timeout=300,
                           headers={"Authorization": f"Bearer {self.key}"})
        except httpx.HTTPError as e:
            raise AIError(f"Нет связи с {self.base}: {type(e).__name__}", "network") from e
        if r.status_code != 200:
            raise http_error("ИИ", r.status_code, r.text)
        d = r.json()
        u = d.get("usage", {})
        return AIResult(d["choices"][0]["message"]["content"], int(u.get("prompt_tokens", 0)),
                        int(u.get("completion_tokens", 0)))
