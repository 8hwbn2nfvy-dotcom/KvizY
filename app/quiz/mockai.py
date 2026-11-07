"""Ответы пробного ИИ (провайдер mock): без интернета и денег, но «по-настоящему» —
вопросы собираются из предложений материала, цитаты дословные. Нужен для тестов и
чтобы проверить сайт без ключа.
"""
from __future__ import annotations

import re

from app.quiz.material import norm_for_match

FAKE = ["квазиморфизм", "пентаграммон", "ортоклазия", "мегалитон", "субфрактал", "гиперлинза"]


def _sentences(material: str) -> list[str]:
    text = re.sub(r"\[(Файл \d+: [^\]]*|Страница \d+|Слайд \d+)\]", "\n", material)
    text = re.sub(r"(?m)^## .*$", "\n", text)
    out = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", text):
        s = s.strip()
        if len(re.findall(r"\w+", s)) >= 6:
            out.append(s)
    return out or [s.strip() for s in text.split("\n") if s.strip()] or ["Материал пустой"]


def _headings(material: str) -> list[str]:
    hs = [h.strip() for h in re.findall(r"(?m)^## (.+)$", material)]
    return hs[:4] or ["Основное"]


def _long_words(s: str) -> list[str]:
    return [w for w in re.findall(r"[А-Яа-яЁёA-Za-z]{5,}", s)]


def _make(qtype: str, i: int, sents: list[str], topic: str, sub: str, avoid: list[str], words: list[str]) -> dict:
    n = len(sents)
    avoid_n = {norm_for_match(a) for a in avoid}
    variants = [(shift, kwi) for kwi in range(3) for shift in range(n)]
    for shift, kwi in variants:
        s = sents[(i + shift) % n]
        kw = sorted(_long_words(s), key=len, reverse=True)
        key = kw[kwi % len(kw)] if kw else "материал"
        q = {"type": qtype, "topic": topic, "subtopic": sub, "flag": "logic" if i % 4 == 3 else "",
             "quote": s, "explanation": f"Так написано в материале: «{s[:120]}».",
             "hint": "Вспомни, о чём говорится рядом с этим местом в материале." if i % 4 == 3 else ""}
        others = [w for w in words if w.lower() != key.lower()]
        if qtype == "single":
            q["text"] = f"Какое слово пропущено: «{s.replace(key, '___', 1)}»?"
            q["options"] = [key] + (others[i % max(1, len(others)):] + others + FAKE)[:3]
            if len({o.lower() for o in q["options"]}) < 4:
                q["options"] = [key] + FAKE[:3]
            q["correct"] = 0
        elif qtype == "multi":
            w2 = kw[1] if len(kw) > 1 else (others[0] if others else "текст")
            q["text"] = f"Какие слова есть во фразе из материала: «{s[:60]}…»?" + (f" Вариант {kwi + 1}." if kwi else "")
            q["options"] = [key, w2, FAKE[i % 6], FAKE[(i + 1) % 6]]
            q["correct"] = [0, 1]
        elif qtype == "tf":
            q["text"] = f"Верно ли, что в материале сказано: «{s}»?" if kwi == 0 else \
                f"Верно ли, что в материале есть фраза со словом «{key}»: «{s}»?"
            q["correct"] = True
        elif qtype == "open":
            q["text"] = f"Объясни своими словами, о чём эта мысль: «{' '.join(s.split()[:8])}…»" + (f" (особенно слово «{key}»)" if kwi else "")
            q["reference"] = s
            q["key_points"] = [key]
        elif qtype == "match":
            trio = [sents[(i + shift + k) % n] for k in range(3)]
            if len(set(trio)) < 3:
                q["type"] = qtype = "tf"
                q["text"] = f"Верно ли, что в материале сказано: «{s}»?"
                q["correct"] = True
            else:
                q["text"] = "Соотнеси начало и конец фраз из материала"
                q["pairs"] = [{"left": " ".join(t.split()[:3]), "right": " ".join(t.split()[-3:])} for t in trio]
        elif qtype == "order":
            trio = [sents[(i + shift + k) % n] for k in range(3)]
            q["text"] = "Расставь фразы в том порядке, как они идут в материале"
            q["items"] = [" ".join(t.split()[:5]) for t in trio]
            if len(set(q["items"])) < 3:
                q["type"] = "fill"
                q["text"] = s.replace(key, "___", 1)
                q["accepted"] = [key]
        elif qtype == "fill":
            q["text"] = s.replace(key, "___", 1)
            q["accepted"] = [key]
        from app.quiz.questions import is_duplicate
        if norm_for_match(q.get("text", "")) not in avoid_n and not is_duplicate(q.get("text", ""), avoid):
            return q
    return q


def quiz(material: str, n: int, plan: dict[str, int], offset: int = 0, avoid: list[str] | None = None) -> dict:
    sents = _sentences(material)
    heads = _headings(material)
    words = list(dict.fromkeys(w for s in sents for w in _long_words(s)))[:50]
    types = [t for t, c in plan.items() for _ in range(c)]
    while len(types) < n:
        types.append("single")
    topics = [{"name": h, "subtopics": [f"{h}: часть 1", f"{h}: часть 2"]} for h in heads]
    qs = []
    for i, t in enumerate(types[:n]):
        top = topics[i % len(topics)]
        qs.append(_make(t, i + offset, sents, top["name"], top["subtopics"][(i // len(topics)) % 2], avoid or [], words))
    return {"topics": topics, "questions": qs}


def replace(material: str, slots: list[dict]) -> dict:
    sents = _sentences(material)
    words = list(dict.fromkeys(w for s in sents for w in _long_words(s)))[:50]
    out = []
    for k, s in enumerate(slots):
        avoid = s.get("avoid", [])
        q = _make(s["type"], 7 + k + len(avoid) * 3, sents, s.get("topic", ""), s.get("subtopic", ""), avoid, words)
        q["slot"] = s["slot"]
        q["flag"] = s.get("flag", "")
        out.append(q)
    return {"questions": out}


def check_open(reference: str, answer: str) -> dict:
    a = {w for w in norm_for_match(answer).split() if len(w) > 3}
    r = {w for w in norm_for_match(reference).split() if len(w) > 3}
    share = len(a & r) / max(1, len(r))
    score = 1 if share >= 0.6 else 0.5 if share >= 0.3 else 0
    return {"score": score, "comment": f"Совпало главных слов: {round(share * 100)} %."}
