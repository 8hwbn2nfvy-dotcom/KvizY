"""Проверка ответов кодом и подсчёт итогов. ИИ здесь участвует только в открытых ответах
(см. pipeline.check_open). Всё остальное — чистый код.

Каждая проверка возвращает долю от 0 до 1. «Полностью верно» (зелёная карточка) — только 1.
"""
from __future__ import annotations

import re

from app.quiz.material import norm_for_match


def _lev(a: str, b: str, limit: int = 3) -> int:
    """Расстояние Левенштейна (сколько букв поправить), с ранней остановкой."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def fill_ok(given: str, accepted: list[str]) -> bool:
    g = norm_for_match(given)
    if not g:
        return False
    for a in accepted:
        n = norm_for_match(a)
        if g == n:
            return True
        # допускаем опечатку: 1 буква в словах от 5 букв, 2 — от 9
        allowed = 2 if len(n) >= 9 else 1 if len(n) >= 5 else 0
        if allowed and _lev(g, n, allowed) <= allowed:
            return True
    return False


def grade(qtype: str, options, answer: dict, response: dict) -> float:
    """Доля баллов за ответ (0..1). Открытые ответы сюда не попадают."""
    r = response or {}
    if r.get("timeout"):
        return 0.0
    if qtype == "single":
        return 1.0 if r.get("choice") in answer.get("correct", []) else 0.0
    if qtype == "multi":
        correct = set(answer.get("correct", []))
        chosen = set(r.get("choices") or [])
        if chosen == correct:
            return 1.0
        hits, wrong = len(chosen & correct), len(chosen - correct)
        return round(max(0.0, (hits - wrong) / max(1, len(correct))), 3)
    if qtype == "tf":
        return 1.0 if r.get("value") is not None and bool(r.get("value")) == answer.get("value") else 0.0
    if qtype == "match":
        pairs = answer.get("pairs", [])
        given = r.get("right") or []  # для каждого левого — выбранный правый
        ok = sum(1 for i, (_, right) in enumerate(pairs) if i < len(given) and given[i] == right)
        return round(ok / max(1, len(pairs)), 3)
    if qtype == "order":
        correct = answer.get("order", [])
        given = r.get("order") or []
        if given == correct:
            return 1.0
        ok = sum(1 for i, item in enumerate(correct) if i < len(given) and given[i] == item)
        return round(ok / max(1, len(correct)), 3)
    if qtype == "fill":
        return 1.0 if fill_ok(str(r.get("text", "")), answer.get("accepted", [])) else 0.0
    return 0.0


def describe_response(qtype: str, options, response: dict) -> str:
    """Ответ человека словами — для итога."""
    r = response or {}
    if r.get("timeout"):
        return "время вышло"
    try:
        if qtype == "single":
            return options[r["choice"]] if r.get("choice") is not None else "—"
        if qtype == "multi":
            return "; ".join(options[i] for i in r.get("choices", [])) or "—"
        if qtype == "tf":
            return "—" if r.get("value") is None else ("Верно" if r["value"] else "Неверно")
        if qtype == "match":
            left = options.get("left", [])
            return "; ".join(f"{l} → {rv or '—'}" for l, rv in zip(left, r.get("right", [])))
        if qtype == "order":
            return " → ".join(r.get("order", [])) or "—"
        if qtype in ("fill", "open"):
            return str(r.get("text", "")).strip() or "—"
    except (IndexError, KeyError, TypeError):
        pass
    return "—"


def describe_answer(qtype: str, options, answer: dict) -> str:
    """Правильный ответ словами — для итога."""
    try:
        if qtype in ("single", "multi"):
            return "; ".join(options[i] for i in answer.get("correct", []))
        if qtype == "tf":
            return "Верно" if answer.get("value") else "Неверно"
        if qtype == "match":
            return "; ".join(f"{a} → {b}" for a, b in answer.get("pairs", []))
        if qtype == "order":
            return " → ".join(answer.get("order", []))
        if qtype == "fill":
            return " / ".join(answer.get("accepted", []))
        if qtype == "open":
            ref = answer.get("reference", "")
            kp = answer.get("key_points") or []
            return ref + (("\nГлавное: " + "; ".join(kp)) if kp else "")
    except (IndexError, KeyError, TypeError):
        pass
    return ""


def pct(score: float, max_score: float) -> float:
    return round(100.0 * score / max_score, 1) if max_score else 0.0


def summarize(rows: list[tuple[str, str, float, float]]) -> tuple[dict, dict]:
    """rows: (тема, подтема, баллы, максимум) → (% по темам, % по «тема / подтема»)."""
    topics: dict[str, dict] = {}
    subs: dict[str, dict] = {}
    for topic, sub, sc, mx in rows:
        topic = topic or "Без темы"
        t = topics.setdefault(topic, {"score": 0.0, "max": 0.0})
        t["score"] += sc
        t["max"] += mx
        if sub:
            key = f"{topic} / {sub}"
            s = subs.setdefault(key, {"topic": topic, "subtopic": sub, "score": 0.0, "max": 0.0})
            s["score"] += sc
            s["max"] += mx
    for d in list(topics.values()) + list(subs.values()):
        d["score"] = round(d["score"], 2)
        d["pct"] = pct(d["score"], d["max"])
    return topics, subs


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()
