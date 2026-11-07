"""Весь путь с пробным ИИ: вход → квизы из PDF, Word, .pptx, txt и ссылки → прохождение →
замена вопроса (лимит 4) → итог → статистика → квиз по предмету → удаление."""
from __future__ import annotations

import http.server
import threading

import pytest

import samples


def upload(client, name: str, data: bytes, part: int = 2 * 1024 * 1024) -> str:
    upload_id = ""
    for n, i in enumerate(range(0, len(data), part)):
        r = client.post("/api/uploads", data={"upload_id": upload_id, "n": str(n), "filename": name},
                        files={"part": (name, data[i:i + part])})
        assert r.status_code == 200, r.text
        upload_id = r.json()["upload_id"]
    return upload_id


def make_quiz(client, subject="Химия", topic="Тест", count=10, **kw) -> str:
    body = {"subject": subject, "topic": topic, "count": count, "difficulty": "medium", "timer": False,
            "hints": True, **kw}
    r = client.post("/api/quizzes", json=body)
    assert r.status_code == 200, r.text
    qid = r.json()["quiz_id"]
    r = client.post(f"/api/quizzes/{qid}/generate")
    assert r.status_code == 200, r.text
    st = client.get(f"/api/quizzes/{qid}/status").json()
    assert st["status"] == "ready", st
    return qid


def play(client, quiz_id: str, right: bool = True) -> dict:
    """Пройти квиз: отвечаем правильно (по данным из базы) или нарочно неправильно."""
    from app.db import Question, SessionLocal, loads
    att = client.post(f"/api/quizzes/{quiz_id}/attempts").json()
    with SessionLocal() as s:
        qs = {q.id: q for q in s.query(Question).filter(Question.quiz_id == quiz_id)}
    for pq in att["questions"]:
        q = qs[pq["id"]]
        a = loads(q.answer)
        if q.qtype == "single":
            resp = {"choice": a["correct"][0] if right else (a["correct"][0] + 1) % 4}
        elif q.qtype == "multi":
            resp = {"choices": a["correct"] if right else []}
        elif q.qtype == "tf":
            resp = {"value": a["value"] if right else not a["value"]}
        elif q.qtype == "open":
            resp = {"text": a["reference"] if right else "не знаю"}
        elif q.qtype == "match":
            resp = {"right": [p[1] for p in a["pairs"]] if right else []}
        elif q.qtype == "order":
            resp = {"order": a["order"] if right else list(reversed(a["order"]))}
        else:
            resp = {"text": a["accepted"][0] if right else "абракадабра"}
        r = client.post(f"/api/attempts/{att['attempt_id']}/answer",
                        json={"question_id": q.id, "response": resp, "time_sec": 3})
        assert r.status_code == 200, r.text
        assert r.json()["correct"] is right, (q.qtype, resp, r.json())
    return client.post(f"/api/attempts/{att['attempt_id']}/finish", json={"duration": 30}).json()


@pytest.fixture(scope="module")
def site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = samples.HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/page"
    srv.shutdown()


def test_health_and_login(app_client):
    assert app_client.get("/api/health").json()["ok"] is True
    assert app_client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert app_client.post("/api/login", json={"password": "test-pass"}).status_code == 200
    assert app_client.get("/api/bootstrap").status_code == 200


def test_unauthorized():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/quizzes").status_code == 401


@pytest.mark.parametrize("kind", ["pdf", "docx", "pptx", "txt"])
def test_quiz_from_file(client, kind):
    data = {"pdf": samples.pdf_bytes, "docx": samples.docx_bytes, "pptx": samples.pptx_bytes, "txt": samples.txt_bytes}[kind]()
    if data is None:
        pytest.skip("нет шрифта для PDF")
    up = upload(client, f"lecture.{kind}", data, part=4000)  # маленькие куски — проверяем склейку
    qid = make_quiz(client, topic=f"Из {kind}", uploads=[up], count=8)
    d = client.get(f"/api/quizzes/{qid}").json()
    assert d["questions"] == 8
    assert d["topics"]
    res = play(client, qid, right=True)
    assert res["pct"] == 100.0


def test_pdf_homoglyph_fixed():
    data = samples.pdf_bytes()
    if data is None:
        pytest.skip("нет шрифта")
    from app.quiz.material import extract_file
    text = extract_file("x.pdf", data)
    assert "катоде" in text and "ĸ" not in text


def test_quiz_from_url_and_full_path(client, site):
    qid = make_quiz(client, subject="  химия ", topic="Со ссылки", urls=[site], count=6)
    d = client.get(f"/api/quizzes/{qid}").json()
    assert d["subject"] == "Химия"  # «  химия » и «Химия» — один предмет
    assert d["sources"][0]["kind"] == "url"

    # прохождение с ошибками → итог красный
    res = play(client, qid, right=False)
    assert res["pct"] == 0
    assert all(not c["correct"] for c in res["cards"])
    assert res["cards"][0]["explanation"]

    # замена вопроса: до 4 раз
    att = client.post(f"/api/quizzes/{qid}/attempts").json()
    q0 = att["questions"][0]
    texts = {q0["text"]}
    prev_type = q0["qtype"]
    changed = 0
    for _ in range(8):
        r = client.post(f"/api/questions/{q0['id']}/replace").json()
        if r["changed"]:
            changed += 1
            assert r["question"]["qtype"] != prev_type  # при замене меняется и вид вопроса
            prev_type = r["question"]["qtype"]
            assert r["question"]["text"] not in texts
            texts.add(r["question"]["text"])
        else:
            print("NOT CHANGED:", r["message"]); assert r["message"]
        if changed == 4:
            break
    assert changed == 4
    r = client.post(f"/api/questions/{q0['id']}/replace").json()
    assert r["changed"] is False and "больше нельзя" in r["message"]

    # правильно → 100 %
    res = play(client, qid, right=True)
    assert res["pct"] == 100.0
    by_topic = res["by_topic"]
    assert by_topic and all(v["pct"] == 100.0 for v in by_topic.values())

    # статистика и рост
    st = client.get("/api/stats").json()
    assert st["topics"] and st["history"] and len(st["growth"]) >= 2
    detail = client.get(f"/api/quizzes/{qid}").json()
    assert detail["best_pct"] == 100.0 and detail["last_pct"] == 100.0


def test_subject_quiz_and_delete(client):
    subjects = client.get("/api/subjects").json()
    chem = next(s for s in subjects if s["name"] == "Химия")
    info = client.get(f"/api/subjects/{chem['id']}").json()
    assert info["pool"] > 0
    r = client.post(f"/api/subjects/{chem['id']}/quiz", json={"count": 10, "difficulty": "hard", "timer": True, "hints": False})
    assert r.status_code == 200, r.text
    sq = r.json()["quiz_id"]
    assert client.post(f"/api/quizzes/{sq}/generate").status_code == 200
    from app.db import Question, SessionLocal
    with SessionLocal() as s:
        qs = s.query(Question).filter(Question.quiz_id == sq).all()
    assert len(qs) == 10
    old = [q for q in qs if q.source_question_id]
    assert len(old) == 5 and len(qs) - len(old) == 5
    assert all(not q.hint for q in qs)  # подсказки выключены
    res = play(client, sq, right=True)
    assert res["pct"] == 100.0

    # удаление обычного квиза: исчезает вместе с вопросами, попытками и текстом
    quizzes = client.get("/api/quizzes").json()
    victim = next(q for q in quizzes if q["topic"] == "Из txt")
    with SessionLocal() as s:
        pool_before = s.query(Question).count()
    assert client.delete(f"/api/quizzes/{victim['id']}").status_code == 200
    assert client.get(f"/api/quizzes/{victim['id']}").status_code == 404
    from app.db import Attempt, QuizText
    with SessionLocal() as s:
        assert s.query(Question).filter(Question.quiz_id == victim["id"]).count() == 0
        assert s.query(Attempt).filter(Attempt.quiz_id == victim["id"]).count() == 0
        assert s.query(QuizText).filter(QuizText.quiz_id == victim["id"]).count() == 0
        assert s.query(Question).count() < pool_before


def test_usage_and_settings(client):
    u = client.get("/api/usage").json()
    assert u["budget"] == 500 and any(p["purpose"] == "quiz" for p in u["by_purpose"])
    # ключ: записали — обратно не отдаётся
    r = client.put("/api/settings/ai", json={"provider": "yandex", "fields": {"folder_id": "b1gtest"},
                                             "secrets": {"api_key": "SECRET-123"}})
    assert r.status_code == 200
    assert "SECRET-123" not in r.text
    s = client.get("/api/settings").json()
    assert s["ai"]["providers"]["yandex"]["secrets"]["api_key"]["set"] is True
    assert "SECRET-123" not in str(s)
    from app import store
    assert store.provider_config("yandex")["api_key"] == "SECRET-123"
    from app.db import SessionLocal, Setting
    with SessionLocal() as db:
        raw = db.get(Setting, "owner:key.yandex.api_key").value
    assert "SECRET-123" not in raw  # в базе зашифрован
    client.delete("/api/settings/ai/yandex/api_key")
    assert client.get("/api/settings").json()["ai"]["providers"]["yandex"]["secrets"]["api_key"]["set"] is False
    # вернуть пробный ИИ
    client.put("/api/settings/ai", json={"provider": "mock"})
    assert client.post("/api/settings/ai/check", json={"provider": "mock"}).json()["ok"] is True
    assert client.put("/api/settings/palette", json={"palette": "neon"}).status_code == 200


def test_open_answer_fallback(client, monkeypatch):
    """Если ИИ не смог проверить открытый ответ — не падаем, а спрашиваем «засчитать?»."""
    from app.ai.base import AIError
    from app.db import Question, SessionLocal
    from app.quiz import pipeline
    ready = [q["id"] for q in client.get("/api/quizzes").json() if q["status"] == "ready"]
    with SessionLocal() as s:
        open_q = s.query(Question).filter(Question.quiz_id.in_(ready), Question.qtype == "open").first()
    assert open_q is not None
    qid = open_q.quiz_id
    att = client.post(f"/api/quizzes/{qid}/attempts").json()

    def boom(*a, **k):
        raise AIError("лимит", "limit")

    monkeypatch.setattr(pipeline, "check_open", boom)
    r = client.post(f"/api/attempts/{att['attempt_id']}/answer",
                    json={"question_id": open_q.id, "response": {"text": "мой ответ"}}).json()
    assert r["status"] == "unchecked"
    r = client.post(f"/api/attempts/{att['attempt_id']}/manual", json={"question_id": open_q.id, "accept": "full"}).json()
    assert r["correct"] is True


def test_password_change_logs_out_others(client):
    from fastapi.testclient import TestClient

    from app.main import app
    other = TestClient(app)
    assert other.post("/api/login", json={"password": "test-pass"}).status_code == 200
    assert other.get("/api/quizzes").status_code == 200
    r = client.post("/api/settings/password", json={"old": "test-pass", "new": "new-pass-1"})
    assert r.status_code == 200
    assert other.get("/api/quizzes").status_code == 401   # другое устройство вышло
    assert client.get("/api/quizzes").status_code == 200  # это осталось
    assert other.post("/api/login", json={"password": "test-pass"}).status_code == 401
    assert other.post("/api/login", json={"password": "new-pass-1"}).status_code == 200


def test_login_rate_limit():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app, headers={"x-forwarded-for": "10.9.9.9"})
    for _ in range(5):
        assert c.post("/api/login", json={"password": "bad"}).status_code == 401
    assert c.post("/api/login", json={"password": "bad"}).status_code == 429


def test_review_mistakes(client):
    """Разбор ошибок: делается по кнопке, сохраняется, второй раз ИИ не спрашиваем."""
    quizzes = [q for q in client.get("/api/quizzes").json() if q["status"] == "ready"]
    res = play(client, quizzes[0]["id"], right=False)
    full = client.get(f"/api/attempts/{res['attempt_id']}").json()
    assert full["mistakes"] > 0 and full["review"] is None
    from app.db import SessionLocal, Usage
    with SessionLocal() as s:
        before = s.query(Usage).filter(Usage.purpose == "review").count()
    r = client.post(f"/api/attempts/{res['attempt_id']}/review").json()
    assert r["topics"] and r["topics"][0]["explain"]
    r2 = client.post(f"/api/attempts/{res['attempt_id']}/review").json()
    assert r2 == r
    with SessionLocal() as s:
        assert s.query(Usage).filter(Usage.purpose == "review").count() == before + 1
    assert client.get(f"/api/attempts/{res['attempt_id']}").json()["review"] == r
