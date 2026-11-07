"""Тексты всех запросов к ИИ. Общие правила (понимание, русский, без выдумок, дословная цитата,
только JSON) добавляются автоматически в app/ai/__init__.py (SYSTEM_RULES).

Хочешь поменять, какие вопросы делает ИИ, — правь здесь.
"""
from __future__ import annotations

import json

from app.quiz.questions import DIFFICULTIES, TYPE_NAMES

DIFF_TEXT = {
    "easy": "лёгкий — основные понятия и факты, но всё равно на понимание (не «найди слово»)",
    "medium": "средний — связи, причины и следствия, применение, сравнение",
    "hard": "на размышление — анализ, перенос на новые ситуации, рассуждение в несколько шагов",
}

SCHEMA = """{
  "topics": [{"name": "Тема", "subtopics": ["Подтема 1", "Подтема 2"]}],
  "questions": [
    {"type": "single", "topic": "Тема", "subtopic": "Подтема 1", "flag": "", "text": "Вопрос?",
     "options": ["вариант", "вариант", "вариант", "вариант"], "correct": 2,
     "explanation": "Почему так (2–3 предложения).", "quote": "дословный кусок из материала", "hint": ""},
    {"type": "multi", ..., "options": ["...", "...", "...", "...", "..."], "correct": [0, 3]},
    {"type": "tf", ..., "text": "Утверждение.", "correct": true},
    {"type": "open", ..., "text": "Объясни, почему ...", "reference": "образец ответа", "key_points": ["мысль 1", "мысль 2"]},
    {"type": "match", ..., "text": "Соотнеси ...", "pairs": [{"left": "термин", "right": "что это"}, ... 3–5 пар]},
    {"type": "order", ..., "text": "Расставь по порядку ...", "items": ["шаг 1", "шаг 2", "шаг 3", "шаг 4"]},
    {"type": "fill", ..., "text": "Предложение с пропуском ___ внутри.", "accepted": ["слово", "синоним"]}
  ]
}"""

TYPE_RULES = """Правила по видам:
- single: ровно 4 варианта, ровно 1 правильный, correct — номер правильного (с 0). Неправильные варианты правдоподобные.
- multi: 4–6 вариантов, правильных от 2 и не все; correct — список номеров (с 0).
- tf: утверждение, correct — true или false. Делай и верные, и неверные утверждения.
- open: вопрос, на который отвечают 1–3 предложениями своими словами; ОБЯЗАТЕЛЬНО reference — образец ответа, key_points — 2–4 главные мысли.
- match: 3–5 пар, items в left разные, в right разные.
- order: 3–6 шагов процесса в ПРАВИЛЬНОМ порядке (перемешает программа). Только если в материале есть последовательность.
- fill: одно предложение с ОДНИМ пропуском ___, accepted — правильное слово и допустимые варианты.
Поле flag: "outside" — вопрос чуть шире материала (общеизвестное рядом с темой; quote — самый близкий кусок материала или ""),
"logic" — вопрос на рассуждение, "" — обычный."""


def _plan_text(plan: dict[str, int]) -> str:
    return ", ".join(f"{TYPE_NAMES[t]} ({t}) — {n}" for t, n in plan.items())


def quiz_prompt(*, title: str, subject: str, n: int, difficulty: str, plan: dict[str, int], hints: bool,
                material: str, instruction: str = "", part: tuple[int, int] | None = None,
                known_topics: list[str] | None = None, avoid: list[str] | None = None) -> str:
    flagged = max(1, round(n * 0.25)) if n >= 3 else 0
    lines = [
        f"Сделай квиз по материалу ниже. Предмет: «{subject}». Тема квиза: «{title}».",
        f"Нужно РОВНО {n} вопросов. Сложность: {DIFF_TEXT.get(difficulty, DIFF_TEXT['medium'])}.",
        f"Виды вопросов (примерно; если материал не подходит под вид — замени на другой): {_plan_text(plan)}.",
        "Сначала сам раздели материал на темы (2–6) и, где тема большая, на подтемы (2–4). "
        "Каждый вопрос привяжи к теме и подтеме из этого списка. Вопросы распредели по всем темам.",
        f"Примерно {flagged} вопросов сделай с пометкой flag (\"outside\" или \"logic\").",
        ("У вопросов с пометкой outside/logic добавь hint — подсказку в 1 предложение, которая наводит, "
         "но не выдаёт ответ. У остальных hint пустой.") if hints else "Подсказки не нужны: hint везде пустой.",
        "explanation — коротко, почему ответ такой; quote — дословный кусок материала, на котором держится ответ.",
    ]
    if part:
        lines.append(f"Это часть {part[0]} из {part[1]} большого материала — делай вопросы только по этой части.")
    if known_topics:
        lines.append("Уже есть темы (используй их названия, если подходят): " + "; ".join(known_topics[:12]) + ".")
    if instruction:
        lines.append(f"Указание человека, какую часть материала брать: «{instruction}». Делай вопросы только по этой части.")
    if avoid:
        lines.append("НЕ повторяй эти вопросы (и не делай очень похожих):\n" + "\n".join(f"- {a}" for a in avoid[:60]))
    lines += [TYPE_RULES, "Формат ответа — JSON:", SCHEMA, "Материал:", "<<<", material, ">>>"]
    return "\n".join(lines)


def replace_prompt(*, slots: list[dict], material: str, hints: bool, subject: str, title: str) -> str:
    """slots: [{slot, topic, subtopic, type, difficulty, flag, avoid: [...]}] — по одному новому вопросу на слот.
    type = "any" + not_type — «любой вид, кроме этого» (кнопка «заменить вопрос»)."""
    def kind(s):
        if s["type"] == "any":
            return f"любой вид, КРОМЕ {s.get('not_type')} ({TYPE_NAMES.get(s.get('not_type'), '')}); выбери тот, что лучше подходит к материалу"
        return s["type"]
    want = [{"slot": s["slot"], "type": kind(s), "topic": s.get("topic", ""), "subtopic": s.get("subtopic", ""),
             "difficulty": DIFFICULTIES.get(s.get("difficulty", "medium"), "средний"), "flag": s.get("flag", ""),
             "не_похож_на": s.get("avoid", [])[:25]} for s in slots]
    return "\n".join([
        f"Нужны новые вопросы для квиза «{title}» (предмет «{subject}»). Сделай по одному вопросу на каждый слот ниже.",
        "Новый вопрос — той же темы, подтемы и сложности, что указаны в слоте, вида — как сказано в поле type слота, "
        "и НЕ похожий на вопросы из «не_похож_на» (другой факт или другая сторона темы).",
        "В ответе поле type — один из кодов: single, multi, tf, open, match, order, fill.",
        "У open обязательно заполни reference (образец ответа) и key_points.",
        ("У вопросов с flag outside/logic добавь hint (1 предложение, не выдаёт ответ)." if hints else "hint везде пустой."),
        "Слоты:", json.dumps(want, ensure_ascii=False, indent=1),
        TYPE_RULES,
        'Формат ответа — JSON: {"questions": [{"slot": "…", "type": "…", "topic": "…", "subtopic": "…", "flag": "…", '
        '"text": "…", …поля по виду…, "explanation": "…", "quote": "дословный кусок материала", "hint": ""}]}',
        "Материал:", "<<<", material, ">>>",
    ])


def check_open_prompt(*, question: str, reference: str, key_points: list[str], answer: str) -> str:
    return "\n".join([
        "Проверь ответ ученика на открытый вопрос. Оценивай смысл, а не слова: синонимы и свои формулировки — нормально.",
        "Оценка: 1 — ответ полностью верный (есть главное), 0.5 — частично (верно, но не хватает важного или есть неточность), "
        "0 — неверно, не по теме или пусто.",
        f"Вопрос: {question}",
        f"Образец ответа: {reference}",
        ("Главные мысли: " + "; ".join(key_points)) if key_points else "",
        f"Ответ ученика: «{answer}»",
        'Формат ответа — JSON: {"score": 0 | 0.5 | 1, "comment": "1–2 предложения: что верно и чего не хватает"}',
    ])
