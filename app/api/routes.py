"""Все адреса API. Каждый ответ с ошибкой — {"detail": "понятный текст"} и код 4xx/5xx,
экран показывает его с кнопкой «Повторить»."""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app import ai, auth, config, store
from app.ai.base import AIError
from app.db import OWNER_ID, Question, Quiz, SessionLocal, Subject, UploadPart, dumps, loads, new_id, now
from app.quiz import attempts, pipeline
from app.quiz.material import (MaterialError, apply_range, combine, estimate_tokens, extract_file, fetch_url,
                               norm_subject)

router = APIRouter(prefix="/api")
User = Depends(auth.current_user)


def _err(e: Exception, code: int = 400):
    raise HTTPException(code, str(e)) from e


# --- Вход ---------------------------------------------------------------------------

class LoginIn(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    user_id = auth.check_password(body.password, auth.client_id(request))
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(auth.COOKIE, auth.make_token(user_id), max_age=auth.TTL, httponly=True,
                        samesite="lax", secure=secure)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/bootstrap")
def bootstrap(user_id: str = User):
    """Всё, что нужно экрану при запуске, одним запросом."""
    st = store.get_many()
    with SessionLocal() as s:
        subjects = [x.name for x in s.scalars(sa.select(Subject).order_by(Subject.name)).all()]
    return {"ok": True, "palette": st.get("palette") or "classic", "form": st.get("form") or {},
            "subjects": subjects, "provider": store.active_provider(),
            "limits": {"max_files": config.MAX_FILES, "max_file_mb": config.MAX_FILE_MB,
                       "part_bytes": config.UPLOAD_PART_BYTES, "max_urls": config.MAX_URLS,
                       "choices": config.QUESTION_CHOICES, "max_questions": config.MAX_QUESTIONS,
                       "timer": config.TIMER_SECONDS, "max_replaces": config.MAX_REPLACES}}


# --- Загрузка файлов кусками ---------------------------------------------------------

@router.post("/uploads")
async def upload_part(upload_id: str = Form(""), n: int = Form(0), filename: str = Form(...),
                      part: UploadFile = File(...), user_id: str = User):
    """Файл приходит кусками по ~2 МБ (у Yandex Serverless Containers запрос ограничен ~3,5 МБ).
    Куски временно лежат в базе и удаляются сразу после извлечения текста."""
    data = await part.read()
    if len(data) > config.UPLOAD_PART_BYTES + 1024:
        raise HTTPException(413, "Слишком большой кусок файла")
    upload_id = upload_id or new_id()
    with SessionLocal() as s:
        total = sum(len(p.data or b"") for p in s.scalars(sa.select(UploadPart).where(UploadPart.upload_id == upload_id)))
        if total + len(data) > config.MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(413, f"«{filename}» больше {config.MAX_FILE_MB} МБ")
        s.add(UploadPart(user_id=user_id, upload_id=upload_id, n=n, filename=filename, data=data))
        s.commit()
    return {"upload_id": upload_id}


def _take_upload(upload_id: str) -> tuple[str, bytes]:
    with SessionLocal() as s:
        parts = s.scalars(sa.select(UploadPart).where(UploadPart.upload_id == upload_id).order_by(UploadPart.n)).all()
        if not parts:
            raise MaterialError("Файл не дошёл до сервера. Выбери его ещё раз.")
        name = parts[0].filename or "file"
        data = b"".join(p.data or b"" for p in parts)
        s.execute(sa.delete(UploadPart).where(UploadPart.upload_id == upload_id))
        s.commit()
    return name, data


def _cleanup_uploads() -> None:
    """Забытые куски старше часа — удаляем."""
    with SessionLocal() as s:
        s.execute(sa.delete(UploadPart).where(UploadPart.created_at < now() - dt.timedelta(hours=1)))
        s.commit()


# --- Создание квиза -----------------------------------------------------------------

class QuizIn(BaseModel):
    subject: str
    topic: str
    count: int = 10
    difficulty: str = "medium"
    timer: bool = False
    hints: bool = False
    source_range: str = ""
    text: str = ""
    urls: list[str] = Field(default_factory=list)
    uploads: list[str] = Field(default_factory=list)


@router.post("/quizzes")
def create_quiz(body: QuizIn, user_id: str = User):
    """Шаг 1: достаём текст из файлов и ссылок, обрезаем, сохраняем. Вопросы — шаг 2 (/generate)."""
    if not body.topic.strip():
        raise HTTPException(400, "Впиши тему квиза")
    if not 1 <= body.count <= config.MAX_QUESTIONS:
        raise HTTPException(400, f"Число вопросов — от 1 до {config.MAX_QUESTIONS}")
    if body.difficulty not in config.TIMER_SECONDS:
        raise HTTPException(400, "Неизвестная сложность")
    if len(body.uploads) > config.MAX_FILES:
        raise HTTPException(400, f"Не больше {config.MAX_FILES} файлов")
    urls = [u.strip() for u in body.urls if u.strip()]
    if len(urls) > config.MAX_URLS:
        raise HTTPException(400, f"Не больше {config.MAX_URLS} ссылок")
    try:
        subj = pipeline.get_or_create_subject(body.subject, user_id)
    except pipeline.QuizError as e:
        _err(e)
    sources: list[tuple[str, str]] = []
    names: list[dict] = []
    try:
        for up in body.uploads:
            name, data = _take_upload(up)
            sources.append((name, extract_file(name, data)))
            names.append({"kind": "file", "name": name, "size": len(data)})
        for u in urls:
            title, text = fetch_url(u)
            sources.append((title, text))
            names.append({"kind": "url", "name": title, "url": u})
        if body.text.strip():
            from app.quiz.material import clean_text
            sources.append(("вставленный текст", clean_text(body.text)))
            names.append({"kind": "text", "name": "вставленный текст", "size": len(body.text)})
        if not sources:
            raise MaterialError("Добавь материал: файл, ссылку или текст.")
        material = combine(sources)
        material, note, rest = apply_range(material, body.source_range)
    except MaterialError as e:
        _err(e)
    finally:
        _cleanup_uploads()
    params = {"count": body.count, "difficulty": body.difficulty, "timer": body.timer, "hints": body.hints,
              "range_rest": rest}
    big = pipeline.is_big(material, body.count)
    warnings = ["Материал большой: квиз будет делаться дольше и стоить дороже."] if big else []
    qid = new_id()
    with SessionLocal() as s:
        s.add(Quiz(id=qid, user_id=user_id, subject_id=subj.id, topic=body.topic.strip(), kind="normal",
                   params=dumps(params), source_range=body.source_range, range_note=note, sources=dumps(names),
                   chars=len(material), status="new", status_text="текст готов", warnings=dumps(warnings)))
        s.commit()
    pipeline.save_text(qid, material, user_id)
    # Запоминаем значения формы (кроме файлов).
    store.put("form", {"subject": subj.name, "count": body.count, "difficulty": body.difficulty,
                       "timer": body.timer, "hints": body.hints})
    return {"quiz_id": qid, "big": big, "warnings": warnings, "range_note": note, "range_rest": rest,
            "chars": len(material), "tokens": estimate_tokens(material)}


@router.post("/quizzes/{quiz_id}/generate")
def generate(quiz_id: str, user_id: str = User):
    """Шаг 2: вопросы от ИИ. Запрос долгий; экран параллельно спрашивает /status."""
    try:
        return pipeline.generate(quiz_id)
    except AIError as e:
        raise HTTPException(502, str(e)) from e
    except (pipeline.QuizError, MaterialError) as e:
        _err(e)


@router.get("/quizzes/{quiz_id}/status")
def status(quiz_id: str, user_id: str = User):
    with SessionLocal() as s:
        q = s.get(Quiz, quiz_id)
        if q is None:
            raise HTTPException(404, "Квиз не найден")
        q = pipeline.check_stale(q)
        return {"status": q.status, "status_text": q.status_text, "error": q.error,
                "warnings": loads(q.warnings, [])}


@router.get("/quizzes")
def list_quizzes(user_id: str = User):
    return attempts.quiz_list(user_id)


@router.get("/quizzes/{quiz_id}")
def quiz_detail(quiz_id: str, user_id: str = User):
    try:
        return attempts.quiz_detail(quiz_id)
    except attempts.AttemptError as e:
        _err(e, 404)


@router.delete("/quizzes/{quiz_id}")
def delete_quiz(quiz_id: str, user_id: str = User):
    try:
        pipeline.delete_quiz(quiz_id)
    except pipeline.QuizError as e:
        _err(e, 404)
    return {"ok": True}


# --- Прохождение ----------------------------------------------------------------------

@router.post("/quizzes/{quiz_id}/attempts")
def start_attempt(quiz_id: str, user_id: str = User):
    try:
        return attempts.start(quiz_id, user_id)
    except attempts.AttemptError as e:
        _err(e)


class AnswerIn(BaseModel):
    question_id: str
    response: dict = Field(default_factory=dict)
    time_sec: int | None = None


@router.post("/attempts/{attempt_id}/answer")
def answer(attempt_id: str, body: AnswerIn, user_id: str = User):
    try:
        return attempts.answer(attempt_id, body.question_id, body.response, body.time_sec, user_id)
    except attempts.AttemptError as e:
        _err(e)


class ManualIn(BaseModel):
    question_id: str
    accept: str  # full | half | no


@router.post("/attempts/{attempt_id}/manual")
def manual(attempt_id: str, body: ManualIn, user_id: str = User):
    try:
        return attempts.manual_open(attempt_id, body.question_id, body.accept)
    except attempts.AttemptError as e:
        _err(e)


class RecheckIn(BaseModel):
    question_id: str


@router.post("/attempts/{attempt_id}/recheck")
def recheck(attempt_id: str, body: RecheckIn, user_id: str = User):
    try:
        return attempts.recheck_open(attempt_id, body.question_id, user_id)
    except attempts.AttemptError as e:
        _err(e)


class FinishIn(BaseModel):
    duration: int | None = None


@router.post("/attempts/{attempt_id}/finish")
def finish(attempt_id: str, body: FinishIn, user_id: str = User):
    try:
        return attempts.finish(attempt_id, body.duration)
    except attempts.AttemptError as e:
        _err(e)


@router.get("/attempts/{attempt_id}")
def result(attempt_id: str, user_id: str = User):
    try:
        return attempts.result(attempt_id)
    except attempts.AttemptError as e:
        _err(e, 404)


@router.post("/questions/{question_id}/replace")
def replace(question_id: str, user_id: str = User):
    try:
        return pipeline.replace_question(question_id)
    except AIError as e:
        raise HTTPException(502, str(e)) from e
    except pipeline.QuizError as e:
        _err(e)


# --- Статистика и предметы ------------------------------------------------------------

@router.get("/stats")
def stats(user_id: str = User):
    return attempts.stats()


@router.get("/subjects")
def subjects(user_id: str = User):
    return attempts.subjects()


@router.get("/subjects/{subject_id}")
def subject(subject_id: str, user_id: str = User):
    try:
        return attempts.subject_detail(subject_id)
    except attempts.AttemptError as e:
        _err(e, 404)


class SubjectQuizIn(BaseModel):
    count: int = 10
    difficulty: str = "medium"
    timer: bool = False
    hints: bool = False


@router.post("/subjects/{subject_id}/quiz")
def subject_quiz(subject_id: str, body: SubjectQuizIn, user_id: str = User):
    if not 1 <= body.count <= config.MAX_QUESTIONS:
        raise HTTPException(400, f"Число вопросов — от 1 до {config.MAX_QUESTIONS}")
    try:
        qid = pipeline.create_subject_quiz(subject_id, body.model_dump(), user_id)
    except pipeline.QuizError as e:
        _err(e)
    return {"quiz_id": qid}


# --- Настройки ----------------------------------------------------------------------

@router.get("/settings")
def get_settings(user_id: str = User):
    with SessionLocal() as s:
        from app.db import User as U
        u = s.get(U, OWNER_ID)
    return {"ai": store.public_ai_settings(), "palette": store.get("palette") or "classic",
            "palettes": store.PALETTES, "budget": store.budget(),
            "password_source": "settings" if (u and u.password_hash) else "env",
            "default_prices": _default_prices()}


def _default_prices() -> dict:
    out = {}
    for name in store.PROVIDERS:
        try:
            out[name] = ai.get_provider(name).default_price
        except Exception:
            out[name] = (0, 0)
    return out


class AISettingsIn(BaseModel):
    provider: str
    fields: dict = Field(default_factory=dict)
    secrets: dict = Field(default_factory=dict)
    price: dict | None = None


@router.put("/settings/ai")
def save_ai(body: AISettingsIn, user_id: str = User):
    try:
        store.save_ai_settings(body.model_dump())
    except (ValueError, Exception) as e:
        from app.crypto import KeySecretMissing
        if isinstance(e, KeySecretMissing):
            raise HTTPException(400, str(e) + " Попроси добавить KEY_SECRET в переменные контейнера.") from e
        if isinstance(e, ValueError):
            _err(e)
        raise
    return store.public_ai_settings()


@router.delete("/settings/ai/{provider}/{field}")
def delete_key(provider: str, field: str, user_id: str = User):
    if provider not in store.PROVIDERS or field not in store.PROVIDERS[provider]["secret"]:
        raise HTTPException(404, "Нет такого ключа")
    store.delete_secret(provider, field)
    return store.public_ai_settings()


class CheckIn(BaseModel):
    provider: str


@router.post("/settings/ai/check")
def check_ai(body: CheckIn, user_id: str = User):
    if body.provider not in store.PROVIDERS:
        raise HTTPException(400, "Неизвестный провайдер")
    return ai.check_connection(body.provider)


class PaletteIn(BaseModel):
    palette: str


@router.put("/settings/palette")
def set_palette(body: PaletteIn, user_id: str = User):
    if body.palette not in store.PALETTES:
        raise HTTPException(400, "Нет такой палитры")
    store.put("palette", body.palette)
    return {"ok": True}


class BudgetIn(BaseModel):
    budget: float


@router.put("/settings/budget")
def set_budget(body: BudgetIn, user_id: str = User):
    store.put("budget", max(0.0, body.budget))
    return {"ok": True}


class PasswordIn(BaseModel):
    old: str
    new: str


@router.post("/settings/password")
def change_password(body: PasswordIn, response: Response, request: Request, user_id: str = User):
    auth.change_password(body.old, body.new)
    # Все устройства выходят; это устройство сразу получает новый вход, чтобы не вводить пароль ещё раз.
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(auth.COOKIE, auth.make_token(user_id), max_age=auth.TTL, httponly=True,
                        samesite="lax", secure=secure)
    return {"ok": True}


@router.get("/usage")
def usage(user_id: str = User):
    return attempts.usage_summary()


@router.get("/questions/{question_id}")
def question(question_id: str, user_id: str = User):
    from app.quiz.questions import public
    with SessionLocal() as s:
        q = s.get(Question, question_id)
        if q is None:
            raise HTTPException(404, "Вопрос не найден")
        return public(q)


def subject_by_name(name: str):
    with SessionLocal() as s:
        return s.scalars(sa.select(Subject).where(Subject.norm == norm_subject(name))).first()
