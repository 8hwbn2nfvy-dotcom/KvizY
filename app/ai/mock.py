"""Пробный «ИИ» без интернета и без денег (для тестов и проверки сайта без ключа).

Ответ собирает функция mock, которую передаёт тот, кто спрашивает (см. app/quiz/mockai.py).
"""
from __future__ import annotations

import json

from app.ai.base import AIError, AIProvider, AIResult


class MockProvider(AIProvider):
    name = "mock"
    default_price = (0.0, 0.0)
    context_tokens = 32000

    def chat(self, system, user, max_tokens=4000, temperature=0.3, mock=None, **_):
        if mock is None:
            if "ping" in user:
                return AIResult('{"ok": true}', 5, 3)
            raise AIError("Пробный ИИ не умеет отвечать на этот запрос")
        data = mock() if callable(mock) else mock
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        return AIResult(text, len(system + user) // 4, len(text) // 4)
