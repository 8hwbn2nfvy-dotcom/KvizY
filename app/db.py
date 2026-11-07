"""База данных: таблицы и подключение.

Один и тот же код работает с тремя базами:
- пусто в DATABASE_URL  → локальный файл SQLite (data/kvizy.db) — для тестов и запуска на компьютере;
- адрес YDB (как в консоли Yandex Cloud: grpcs://ydb.serverless.yandexcloud.net:2135/?database=/ru-central1/...)
  → YDB Serverless;
- любой адрес SQLAlchemy (sqlite:///..., postgresql+psycopg://...) — тоже можно.

Правила, чтобы всё работало в YDB:
- ключи — строки (uuid), без автоинкремента;
- нет внешних ключей (FOREIGN KEY): связи держит код;
- JSON храним как текст (Text) и разбираем сами;
- большой текст материала режем на части (таблица quiz_texts), потому что в YDB у одной ячейки есть предел.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from urllib.parse import parse_qs, urlparse

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app import config

OWNER_ID = "owner"


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> dt.datetime:
    """Время всегда в UTC и без часового пояса — так одинаково в SQLite и YDB."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def loads(s, default=None):
    if s in (None, ""):
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


class Base(DeclarativeBase):
    pass


Str = sa.String
Txt = sa.Text


class User(Base):
    """Владелец (id = "owner") и тестеры, которых владелец создаёт в Настройках."""
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(Str, primary_key=True)
    password_hash: Mapped[str | None] = mapped_column(Txt)  # у владельца пусто = пароль из KVIZY_PASSWORD
    session_version: Mapped[int | None] = mapped_column(sa.Integer, default=1)
    created_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)
    login: Mapped[str | None] = mapped_column(Str)            # для входа (у владельца — пусто)
    name: Mapped[str | None] = mapped_column(Txt)             # как зовут
    role: Mapped[str | None] = mapped_column(Str)             # owner | guest
    active: Mapped[bool | None] = mapped_column(sa.Boolean)   # выключенный тестер не может войти
    ai_limit_rub: Mapped[float | None] = mapped_column(sa.Double)  # лимит на ИИ в месяц (только тестеры)


class Setting(Base):
    """Настройки «ключ → значение (JSON)». id = user_id + ':' + key."""
    __tablename__ = "settings"
    id: Mapped[str] = mapped_column(Str, primary_key=True)
    user_id: Mapped[str] = mapped_column(Str)
    key: Mapped[str] = mapped_column(Str)
    value: Mapped[str | None] = mapped_column(Txt)
    updated_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)


class Subject(Base):
    __tablename__ = "subjects"
    id: Mapped[str] = mapped_column(Str, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(Str, default=OWNER_ID)
    name: Mapped[str] = mapped_column(Txt)
    norm: Mapped[str] = mapped_column(Txt)  # нормализованное имя: «  Химия » и «химия» — один предмет
    created_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)


class Quiz(Base):
    __tablename__ = "quizzes"
    id: Mapped[str] = mapped_column(Str, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(Str, default=OWNER_ID)
    subject_id: Mapped[str | None] = mapped_column(Str)
    topic: Mapped[str | None] = mapped_column(Txt)          # название квиза
    kind: Mapped[str | None] = mapped_column(Str, default="normal")  # normal | subject
    params: Mapped[str | None] = mapped_column(Txt)         # JSON: count, difficulty, timer, hints
    source_range: Mapped[str | None] = mapped_column(Txt)   # «откуда и докуда» как написал человек
    range_note: Mapped[str | None] = mapped_column(Txt)     # что код обрезал сам
    sources: Mapped[str | None] = mapped_column(Txt)        # JSON: список файлов/ссылок (имена, без содержимого)
    topics: Mapped[str | None] = mapped_column(Txt)         # JSON: [{name, subtopics: [...]}]
    chars: Mapped[int | None] = mapped_column(sa.Integer, default=0)
    status: Mapped[str | None] = mapped_column(Str, default="new")  # new | running | ready | error
    status_text: Mapped[str | None] = mapped_column(Txt)
    error: Mapped[str | None] = mapped_column(Txt)
    warnings: Mapped[str | None] = mapped_column(Txt)       # JSON: список предупреждений
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime)
    best_pct: Mapped[float | None] = mapped_column(sa.Double)
    last_pct: Mapped[float | None] = mapped_column(sa.Double)
    attempts_count: Mapped[int | None] = mapped_column(sa.Integer, default=0)
    created_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)


class QuizText(Base):
    """Извлечённый текст материала, частями. Файлы не храним — только текст."""
    __tablename__ = "quiz_texts"
    id: Mapped[str] = mapped_column(Str, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(Str, default=OWNER_ID)
    quiz_id: Mapped[str] = mapped_column(Str)
    part: Mapped[int] = mapped_column(sa.Integer)
    text: Mapped[str] = mapped_column(Txt)


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(Str, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(Str, default=OWNER_ID)
    quiz_id: Mapped[str] = mapped_column(Str)
    position: Mapped[int] = mapped_column(sa.Integer, default=0)
    topic: Mapped[str | None] = mapped_column(Txt)
    subtopic: Mapped[str | None] = mapped_column(Txt)
    qtype: Mapped[str] = mapped_column(Str)          # single | multi | tf | open | match | order | fill
    difficulty: Mapped[str | None] = mapped_column(Str)
    flag: Mapped[str | None] = mapped_column(Str)    # "" | outside («вне файла») | logic («на логику»)
    text: Mapped[str] = mapped_column(Txt)
    options: Mapped[str | None] = mapped_column(Txt)  # JSON: варианты / пары / шаги
    answer: Mapped[str | None] = mapped_column(Txt)   # JSON: правильный ответ (форма зависит от типа)
    explanation: Mapped[str | None] = mapped_column(Txt)
    quote: Mapped[str | None] = mapped_column(Txt)
    points: Mapped[float | None] = mapped_column(sa.Double, default=1)
    hint: Mapped[str | None] = mapped_column(Txt)
    replace_count: Mapped[int | None] = mapped_column(sa.Integer, default=0)
    replace_history: Mapped[str | None] = mapped_column(Txt)  # JSON: прошлые варианты вопроса
    source_question_id: Mapped[str | None] = mapped_column(Str)  # для квиза по предмету: откуда взят
    created_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[str] = mapped_column(Str, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(Str, default=OWNER_ID)
    quiz_id: Mapped[str] = mapped_column(Str)
    started_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime)
    score: Mapped[float | None] = mapped_column(sa.Double, default=0)
    max_score: Mapped[float | None] = mapped_column(sa.Double, default=0)
    pct: Mapped[float | None] = mapped_column(sa.Double)
    by_topic: Mapped[str | None] = mapped_column(Txt)      # JSON {тема: {score, max, pct}}
    by_subtopic: Mapped[str | None] = mapped_column(Txt)   # JSON {"тема / подтема": {...}}
    duration_sec: Mapped[int | None] = mapped_column(sa.Integer)
    review: Mapped[str | None] = mapped_column(Txt)        # JSON: разбор ошибок от ИИ (кнопка в итоге)


class Answer(Base):
    __tablename__ = "answers"
    id: Mapped[str] = mapped_column(Str, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(Str, default=OWNER_ID)
    attempt_id: Mapped[str] = mapped_column(Str)
    quiz_id: Mapped[str] = mapped_column(Str)
    question_id: Mapped[str] = mapped_column(Str)
    response: Mapped[str | None] = mapped_column(Txt)   # JSON: что ответил человек
    score: Mapped[float | None] = mapped_column(sa.Double, default=0)
    max_score: Mapped[float | None] = mapped_column(sa.Double, default=1)
    correct: Mapped[bool | None] = mapped_column(sa.Boolean, default=False)  # полностью верно
    status: Mapped[str | None] = mapped_column(Str, default="checked")  # checked | unchecked | timeout
    note: Mapped[str | None] = mapped_column(Txt)       # пояснение проверки открытого ответа
    snapshot: Mapped[str | None] = mapped_column(Txt)   # JSON: вопрос на момент ответа (его могли потом заменить)
    time_sec: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)


class Usage(Base):
    __tablename__ = "usage"
    id: Mapped[str] = mapped_column(Str, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(Str, default=OWNER_ID)
    quiz_id: Mapped[str | None] = mapped_column(Str)
    quiz_title: Mapped[str | None] = mapped_column(Txt)  # чтобы расход было видно и после удаления квиза
    provider: Mapped[str | None] = mapped_column(Str)
    model: Mapped[str | None] = mapped_column(Txt)
    purpose: Mapped[str | None] = mapped_column(Str)
    tokens_in: Mapped[int | None] = mapped_column(sa.Integer, default=0)
    tokens_out: Mapped[int | None] = mapped_column(sa.Integer, default=0)
    cost_rub: Mapped[float | None] = mapped_column(sa.Double, default=0)
    created_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)


class UploadPart(Base):
    """Временные куски загружаемых файлов. Удаляются сразу после извлечения текста."""
    __tablename__ = "upload_parts"
    id: Mapped[str] = mapped_column(Str, primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(Str, default=OWNER_ID)
    upload_id: Mapped[str] = mapped_column(Str)
    n: Mapped[int] = mapped_column(sa.Integer)
    filename: Mapped[str | None] = mapped_column(Txt)
    data: Mapped[bytes | None] = mapped_column(sa.LargeBinary)
    created_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)


class LoginFailure(Base):
    __tablename__ = "login_failures"
    id: Mapped[str] = mapped_column(Str, primary_key=True, default=new_id)
    client: Mapped[str | None] = mapped_column(Str)
    at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime, default=now)


# --- Подключение -------------------------------------------------------------------

def _ydb_credentials():
    """Ключ доступа к YDB.
    В Yandex Cloud контейнер берёт токен своего сервисного аккаунта из «метаданных» сам.
    Локально (local-ydb в Docker) — без ключа."""
    import os

    import ydb
    import ydb.iam

    if os.environ.get("YDB_ANONYMOUS_CREDENTIALS") == "1":
        return ydb.AnonymousCredentials()
    sa_file = os.environ.get("YDB_SERVICE_ACCOUNT_KEY_FILE", "").strip()
    if sa_file:
        return ydb.iam.ServiceAccountCredentials.from_file(sa_file)
    return ydb.iam.MetadataUrlCredentials()


def make_engine(url: str = ""):
    url = (url or "").strip()
    if not url:
        config.settings.data_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{config.settings.data_dir / 'kvizy.db'}"
    if url.startswith(("grpc://", "grpcs://")):
        # Адрес из консоли YDB: grpcs://host:2135/?database=/ru-central1/...
        u = urlparse(url)
        database = (parse_qs(u.query).get("database") or [""])[0] or u.path
        return sa.create_engine(
            f"yql+ydb://{u.hostname}:{u.port or 2135}{database}",
            connect_args={"protocol": u.scheme, "credentials": _ydb_credentials()},
            _add_declare_for_yql_stmt_vars=True,
            pool_pre_ping=True,
            pool_size=5,
        )
    if url.startswith("sqlite"):
        return sa.create_engine(url, connect_args={"check_same_thread": False})
    return sa.create_engine(url, pool_pre_ping=True)


engine = None
SessionLocal = sessionmaker(expire_on_commit=False)


def init_engine(url: str | None = None):
    """Создать подключение (при старте сервера и в тестах)."""
    global engine
    engine = make_engine(config.settings.database_url if url is None else url)
    SessionLocal.configure(bind=engine)
    return engine


def is_ydb() -> bool:
    return engine is not None and engine.dialect.name in ("yql", "ydb")
