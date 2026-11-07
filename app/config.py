"""Все настройки сервера. Берутся из переменных окружения (в Yandex Cloud — у ревизии контейнера).

В коде нет ни одного ключа: если переменная не задана, значение пустое.
Здесь же собраны «ручки», которые удобно менять в коде (время на вопрос, лимиты).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


# --- Ручки, которые меняются в коде -------------------------------------------------

# Время на один вопрос (секунды) по сложности, если таймер включён.
TIMER_SECONDS = {"easy": 30, "medium": 60, "hard": 120}

# Сколько раз можно заменить один вопрос.
MAX_REPLACES = 4

# Загрузка материала.
MAX_FILES = 5
MAX_FILE_MB = 15
# Файлы идут на сервер кусками: у Yandex Serverless Containers запрос не больше ~3,5 МБ.
UPLOAD_PART_BYTES = 2 * 1024 * 1024
MAX_URLS = 5

# Варианты числа вопросов в форме (и «своё» до MAX_QUESTIONS).
QUESTION_CHOICES = [5, 10, 15, 20]
MAX_QUESTIONS = 40

# Доля старых вопросов в квизе по предмету (остальное — новые от ИИ).
SUBJECT_OLD_SHARE = 0.5

# Если задача создания квиза не подавала признаков жизни столько секунд — считаем, что
# сервер перезапустился, и показываем «Повторить».
JOB_STALE_SECONDS = 120


@dataclass
class Settings:
    password: str = field(default_factory=lambda: _env("KVIZY_PASSWORD"))
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET"))
    key_secret: str = field(default_factory=lambda: _env("KEY_SECRET"))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))

    ai_provider: str = field(default_factory=lambda: _env("AI_PROVIDER", "yandex").lower())

    # База: пусто = локальный файл SQLite; адрес YDB (grpcs://...?database=...) = YDB;
    # sqlite:///... или postgresql+psycopg://... — тоже можно.
    # Вместо DATABASE_URL можно задать YDB_ENDPOINT (grpcs://ydb.serverless.yandexcloud.net:2135) и YDB_DATABASE (/ru-central1/...).
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL") or (
        f"{_env('YDB_ENDPOINT').rstrip('/')}/?database={_env('YDB_DATABASE')}" if _env("YDB_ENDPOINT") else ""))

    monthly_budget: float = field(default_factory=lambda: _float("MONTHLY_BUDGET_RUB", 500))

    @property
    def secret_ok(self) -> bool:
        return bool(self.session_secret)


settings = Settings()


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    return settings
