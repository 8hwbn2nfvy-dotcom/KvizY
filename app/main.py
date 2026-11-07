"""Запуск сервера: /api/health, отдача интерфейса (PWA), при старте — подключение к базе и миграции.

Тяжёлые библиотеки (pypdf, python-docx, python-pptx, BeautifulSoup, SDK Claude) импортируются
только когда нужны — так контейнер быстрее просыпается.
"""
from __future__ import annotations

import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from app import db, migrations

STATIC = Path(__file__).resolve().parent.parent / "static"
VERSION = "1.0.0"
_started = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if db.engine is None:
        db.init_engine()
    migrations.migrate(db.engine)
    yield


app = FastAPI(title="KvizY", lifespan=lifespan, docs_url=None, redoc_url=None)

from app.api.routes import router  # noqa: E402

app.include_router(router)


@app.exception_handler(Exception)
async def unexpected(request: Request, exc: Exception):
    traceback.print_exc()
    return JSONResponse({"detail": f"Ошибка на сервере: {type(exc).__name__}: {exc}"}, status_code=500)


@app.get("/api/health")
def health():
    """Проверка, что сервер и база живы. Без входа."""
    import sqlalchemy as sa

    t = time.time()
    try:
        with db.SessionLocal() as s:
            s.execute(sa.select(db.User.id).limit(1)).all()
        db_ok = True
    except Exception as e:  # база недоступна — скажем об этом прямо
        db_ok = f"{type(e).__name__}: {e}"[:300]
    return {"ok": db_ok is True, "db": "ok" if db_ok is True else db_ok, "db_kind": db.engine.dialect.name,
            "db_ms": round((time.time() - t) * 1000), "version": VERSION, "uptime_sec": round(time.time() - _started)}


# --- Интерфейс (PWA) ------------------------------------------------------------------

NO_CACHE = {"Cache-Control": "no-cache"}


def _file(name: str, media: str | None = None, headers: dict | None = None):
    path = (STATIC / name).resolve()
    if not str(path).startswith(str(STATIC)) or not path.is_file():
        return Response("Не найдено", status_code=404)
    return FileResponse(path, media_type=media, headers=headers or NO_CACHE)


@app.get("/")
def index():
    return _file("index.html", "text/html; charset=utf-8")


@app.get("/sw.js")
def service_worker():
    # Service worker должен лежать в корне, чтобы управлять всем приложением.
    return _file("sw.js", "application/javascript", {"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.json")
def manifest():
    return _file("manifest.json", "application/manifest+json")


@app.get("/static/{name:path}")
def static(name: str):
    return _file(name)


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def apple_icon():
    return _file("icons/apple-touch-icon.png", "image/png")


@app.get("/favicon.ico")
def favicon():
    return _file("icons/icon-192.png", "image/png")
