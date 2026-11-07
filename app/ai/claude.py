"""Claude (Anthropic) через официальный SDK. SDK импортируется только при первом запросе —
так сервер быстрее просыпается, если Claude не выбран."""
from __future__ import annotations

import os

from app.ai.base import AIError, AIProvider, AIResult


class ClaudeProvider(AIProvider):
    name = "claude"
    # Цены в $ за 1M токенов → ₽ за 1000 токенов по курсу USD_RUB (🟡 курс по умолчанию 100).
    USD = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
    context_tokens = 200000
    max_output = 16000

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self.key = cfg.get("api_key", "")
        rate = float(os.environ.get("USD_RUB", "100") or 100)
        usd_in, usd_out = self.USD.get(self.model, (4.0, 20.0))
        self.default_price = (usd_in * rate / 1000, usd_out * rate / 1000)

    def is_configured(self) -> bool:
        return bool(self.key)

    def chat(self, system, user, max_tokens=8000, temperature=0.3, **_):
        if not self.is_configured():
            raise AIError("Не задан ключ Claude. Впиши его в Настройках → ИИ.", "config")
        import anthropic

        client = anthropic.Anthropic(api_key=self.key, timeout=600)
        kwargs = dict(model=self.model, max_tokens=max(max_tokens, 16000), system=system,
                      messages=[{"role": "user", "content": user}],
                      output_config={"effort": "medium"})
        if self.model in ("claude-opus-5-5", "claude-sonnet-5-5"):
            # Если модель откажется отвечать, сервер сам повторит запрос на запасной модели.
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            resp = client.beta.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise AIError("Claude: ключ не подошёл.", "auth") from e
        except anthropic.PermissionDeniedError as e:
            raise AIError(f"Claude: нет прав или денег на счёте: {e.message}", "money") from e
        except anthropic.RateLimitError as e:
            raise AIError("Claude: превышен лимит запросов. Подожди и нажми «Повторить».", "limit") from e
        except anthropic.APIStatusError as e:
            low = str(e.message).lower()
            kind = "money" if "credit" in low or "billing" in low else "other"
            raise AIError(f"Claude ответил {e.status_code}: {e.message}", kind) from e
        except anthropic.APIConnectionError as e:
            raise AIError("Нет связи с Claude", "network") from e
        if resp.stop_reason == "refusal":
            raise AIError("Claude отказался выполнять запрос")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return AIResult(text, resp.usage.input_tokens, resp.usage.output_tokens)
