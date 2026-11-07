"""Общие настройки тестов: пробный ИИ (mock), временная база.

Обычно база — SQLite во временной папке. Чтобы прогнать тесты на настоящей YDB (local-ydb в Docker):
    KVIZY_TEST_DB=grpc://localhost:2136/?database=/local YDB_ANONYMOUS_CREDENTIALS=1 pytest
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

_tmp = tempfile.mkdtemp(prefix="kvizy-test-")
os.environ.update({
    "DATA_DIR": _tmp, "KVIZY_PASSWORD": "test-pass", "SESSION_SECRET": "s" * 32, "KEY_SECRET": "k" * 32,
    "AI_PROVIDER": "mock", "DATABASE_URL": os.environ.get("KVIZY_TEST_DB", ""),
})


@pytest.fixture(scope="session")
def app_client():
    from fastapi.testclient import TestClient

    from app import config, db
    config.reload_settings()
    db.init_engine()
    if db.is_ydb():  # чистая база для каждого прогона
        db.Base.metadata.drop_all(db.engine)
        from app.migrations import schema_migrations
        schema_migrations.drop(db.engine, checkfirst=True)
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def client(app_client):
    r = app_client.post("/api/login", json={"password": "test-pass"})
    assert r.status_code == 200, r.text
    return app_client
