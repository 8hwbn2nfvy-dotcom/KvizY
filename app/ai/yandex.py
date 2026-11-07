"""YandexGPT через Yandex Cloud Foundation Models API.

Ключ: API-ключ сервисного аккаунта (роль ai.languageModels.user) + ID каталога.
Если ключ не задан, а сервер работает в Yandex Cloud с сервисным аккаунтом, берём
временный IAM-токен этого аккаунта из «метаданных» — тогда ключ вообще не нужен.
"""
from __future__ import annotations

import time

import httpx

from app.ai.base import AIError, AIProvider, AIResult, http_error

URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
METADATA = "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token"

_iam = {"token": "", "exp": 0.0}


def _metadata_token() -> str:
    if _iam["token"] and time.time() < _iam["exp"] - 60:
        return _iam["token"]
    try:
        r = httpx.get(METADATA, headers={"Metadata-Flavor": "Google"}, timeout=2)
        r.raise_for_status()
        d = r.json()
    except Exception:
        return ""
    _iam["token"] = d.get("access_token", "")
    _iam["exp"] = time.time() + int(d.get("expires_in", 600))
    return _iam["token"]


class YandexProvider(AIProvider):
    name = "yandex"
    # 🟡 Примерные цены (синхронный режим), ₽ за 1000 токенов. Точные можно вписать в настройках.
    PRICES = {"yandexgpt-lite": (0.2, 0.2), "yandexgpt": (1.2, 1.2)}
    context_tokens = 32000   # у моделей 5-го поколения запрос + ответ ≤ 32 000 токенов
    max_output = 8000

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.key = cfg.get("api_key", "")
        self.folder = cfg.get("folder_id", "")
        self.default_price = self.PRICES.get(self.model.split("/")[0], (1.2, 1.2))

    def is_configured(self) -> bool:
        return bool(self.folder)

    def _auth(self) -> str:
        if self.key:
            return f"Api-Key {self.key}"
        token = _metadata_token()
        if token:
            return f"Bearer {token}"
        raise AIError("Не задан API-ключ YandexGPT. Впиши его в Настройках → ИИ.", "config")

    def chat(self, system, user, max_tokens=4000, temperature=0.3, **_):
        if not self.folder:
            raise AIError("Не задан ID каталога Yandex Cloud. Впиши его в Настройках → ИИ.", "config")
        model = self.model if "://" in self.model else f"gpt://{self.folder}/{self.model}"
        body = {
            "modelUri": model,
            "completionOptions": {"stream": False, "temperature": temperature,
                                  "maxTokens": str(min(max_tokens, self.max_output))},
            "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
        }
        try:
            r = httpx.post(URL, json=body, timeout=300,
                           headers={"Authorization": self._auth(), "x-folder-id": self.folder})
        except httpx.HTTPError as e:
            raise AIError(f"Нет связи с YandexGPT: {type(e).__name__}", "network") from e
        if r.status_code != 200:
            raise http_error("YandexGPT", r.status_code, r.text)
        data = r.json()["result"]
        usage = data.get("usage", {})
        return AIResult(data["alternatives"][0]["message"]["text"],
                        int(usage.get("inputTextTokens", 0)), int(usage.get("completionTokens", 0)))
