"""Аккаунты для тестеров: создаёт владелец, у каждого свои данные, лимит на ИИ, выключение."""
from fastapi.testclient import TestClient


def guest_client(login, password):
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return c


def test_guest_isolation_and_limits(client):
    # владельцу нужен свой квиз, чтобы проверить, что тестер его не видит
    r = client.post("/api/quizzes", json={"subject": "Секрет", "topic": "Мой личный", "count": 3,
                                          "text": "Электролиз — это процесс разложения вещества током в растворе. "
                                                  "На катоде идёт восстановление ионов металла из раствора соли. "
                                                  "На аноде идёт окисление ионов, и выделяется газ при этом."})
    owner_quiz = r.json()["quiz_id"]
    assert client.post(f"/api/quizzes/{owner_quiz}/generate").status_code == 200

    r = client.post("/api/users", json={"name": "Маша", "login": "Masha", "limit": 50})
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["login"] == "masha" and len(g["password"]) >= 8 and g["limit"] == 50
    assert client.post("/api/users", json={"login": "masha"}).status_code == 400  # логин занят

    m = guest_client("masha", g["password"])
    boot = m.get("/api/bootstrap").json()
    assert boot["me"]["role"] == "guest" and boot["subjects"] == []
    assert m.get("/api/quizzes").json() == []                       # чужих квизов не видно
    assert m.get(f"/api/quizzes/{owner_quiz}").status_code == 404    # и по ссылке тоже
    assert m.post(f"/api/quizzes/{owner_quiz}/attempts").status_code == 404
    assert m.delete(f"/api/quizzes/{owner_quiz}").status_code == 404
    assert m.get("/api/stats").json()["history"] == []
    # настройки ИИ и расходы — только владелец
    assert m.get("/api/usage").status_code == 403
    assert m.put("/api/settings/ai", json={"provider": "mock"}).status_code == 403
    assert m.get("/api/users").status_code == 403
    st = m.get("/api/settings").json()
    assert st["role"] == "guest" and "ai" not in st and st["my_usage"]["limit"] == 50

    # свой квиз тестер делать может
    r = m.post("/api/quizzes", json={"subject": "Химия", "topic": "Квиз Маши", "count": 3,
                                     "text": "Кислоты в водном растворе отдают ионы водорода и меняют цвет лакмуса. "
                                             "Основания в растворе дают гидроксид-ионы и меняют цвет индикатора. "
                                             "Реакция кислоты с основанием называется нейтрализацией и даёт соль."})
    assert r.status_code == 200, r.text
    mq = r.json()["quiz_id"]
    assert m.post(f"/api/quizzes/{mq}/generate").status_code == 200
    assert [q["topic"] for q in m.get("/api/quizzes").json()] == ["Квиз Маши"]
    assert "Квиз Маши" not in [q["topic"] for q in client.get("/api/quizzes").json()]  # владелец не путается
    assert client.get(f"/api/quizzes/{mq}").status_code == 404

    # лимит: ставим 0 ₽ и подделываем расход — ИИ для тестера закрыт, квиз падает с понятной ошибкой
    client.patch(f"/api/users/{g['id']}", json={"limit": 0})
    from app.db import SessionLocal, Usage
    with SessionLocal() as s:
        s.add(Usage(user_id=g["id"], provider="mock", purpose="quiz", cost_rub=1.0))
        s.commit()
    r = m.post("/api/quizzes", json={"subject": "Химия", "topic": "Ещё", "count": 3, "text": "x " * 50 + "Кислоты меняют цвет лакмуса на красный цвет всегда."})
    q2 = r.json()["quiz_id"]
    r = m.post(f"/api/quizzes/{q2}/generate")
    assert r.status_code == 502 and "Лимит" in r.json()["detail"]
    # у владельца лимита нет
    assert client.post("/api/settings/ai/check", json={"provider": "mock"}).json()["ok"] is True
    people = client.get("/api/usage").json()["by_user"]
    assert any(p["name"] == "Маша" for p in people)

    # новый пароль — старый не подходит, тестер выходит
    r = client.patch(f"/api/users/{g['id']}", json={"new_password": True}).json()
    assert r["password"] != g["password"]
    assert m.get("/api/quizzes").status_code == 401
    m2 = guest_client("masha", r["password"])

    # выключение — сразу выходит и не может войти
    client.patch(f"/api/users/{g['id']}", json={"active": False})
    assert m2.get("/api/quizzes").status_code == 401
    from app.main import app
    assert TestClient(app).post("/api/login", json={"login": "masha", "password": r["password"]}).status_code == 403

    # удаление — исчезают все его данные
    assert client.delete(f"/api/users/{g['id']}").status_code == 200
    from app.db import Quiz
    with SessionLocal() as s:
        assert s.query(Quiz).filter(Quiz.user_id == g["id"]).count() == 0
    assert client.get("/api/users").json() == [] or all(u["id"] != g["id"] for u in client.get("/api/users").json())
