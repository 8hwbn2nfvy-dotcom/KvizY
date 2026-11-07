"""Мелкие проверки: цитаты, нормализация предмета, обрезка «откуда докуда», деление текста,
чистка маркеров списков, терпимый разбор JSON, разбор вопросов, подсчёт баллов."""
from app.ai import parse_json, to_number, unwrap
from app.quiz.material import (QuoteIndex, allocate, apply_range, clean_text, combine, estimate_tokens,
                               fix_homoglyphs, join_parts, nice_subject, norm_subject, split_text, strip_list_marker)
from app.quiz.questions import is_duplicate, normalize, problems, type_plan
from app.quiz.scoring import fill_ok, grade


# --- маркеры списков (урок Priz: годы и числа не трогать) ---
def test_list_markers():
    assert strip_list_marker("- пункт") == "пункт"
    assert strip_list_marker("• пункт") == "пункт"
    assert strip_list_marker("1. пункт") == "пункт"
    assert strip_list_marker("2) пункт") == "пункт"
    assert strip_list_marker("12. пункт") == "пункт"
    assert strip_list_marker("1945 год победы") == "1945 год победы"
    assert strip_list_marker("1945. Конец войны") == "1945. Конец войны"
    assert strip_list_marker("3.14 — это пи") == "3.14 — это пи"
    assert strip_list_marker("2024-й год") == "2024-й год"
    assert strip_list_marker("10 причин") == "10 причин"


def test_clean_text():
    t = clean_text("ĸлетка и мoлекула DNA\nэлек-\nтролиз\n\n\n\n- пункт\n1945 год")
    assert "клетка" in t and "молекула" in t and "DNA" in t
    assert "электролиз" in t
    assert "\n\n\n" not in t
    assert "пункт" in t and "- пункт" not in t
    assert "1945 год" in t


def test_homoglyph_only_in_russian_words():
    assert fix_homoglyphs("Moscow cat") == "Moscow cat"
    assert fix_homoglyphs("кoт") == "кот"


def test_join_parts():
    assert join_parts("Готово.", ".", "Дальше") == "Готово. Дальше"
    assert join_parts("", "А.", "", "Б") == "А. Б"
    assert ".." not in join_parts("Конец..", "начало")


# --- цитаты ---
def test_quotes():
    q = QuoteIndex("Электролиз — это   процесс,\nпри котором ТОК идёт через раствор.")
    assert q.contains("электролиз это процесс, при котором ток идет")
    assert q.contains("«Электролиз — это процесс»… ток идёт через раствор")
    assert not q.contains("ток не идёт совсем никогда")
    assert not q.contains("")
    assert not q.contains("ток")  # слишком короткая «цитата» не считается


# --- предмет ---
def test_subject_norm():
    assert norm_subject("  Химия ") == norm_subject("химия") == norm_subject("ХИМИЯ")
    assert norm_subject("Физика  атома") == "физика атома"
    assert norm_subject("Ёлки") == norm_subject("елки")
    assert nice_subject("  химия  ") == "Химия"


# --- откуда и докуда ---
TEXT = combine([("a.pptx", "\n\n".join(f"[Слайд {i}]\nслайд номер {i}" for i in range(1, 16))),
                ("b.txt", "## Введение\nвведение текст\n## Электролиз\nпро ток\n## Итоги\nконец")])


def test_range_slides():
    t, note, rest = apply_range(TEXT, "со слайда 5 до 12")
    assert "слайд номер 5" in t and "слайд номер 12" in t
    assert "слайд номер 4" not in t and "слайд номер 13" not in t
    assert note and not rest
    t, _, _ = apply_range(TEXT, "с 10 слайда до конца")
    assert "слайд номер 15" in t and "слайд номер 9\n" not in t


def test_range_file_and_section():
    t, note, rest = apply_range(TEXT, "только 2-й файл")
    assert "слайд номер" not in t and "введение" in t and not rest
    t, note, rest = apply_range(TEXT, "с раздела «Электролиз» до конца")
    assert t.startswith("## Электролиз") and "введение" not in t and not rest
    t, note, rest = apply_range(TEXT, "только 2-й файл, с раздела «Электролиз» до раздела «Итоги»")
    assert "про ток" in t and "конец" not in t


def test_range_rest_goes_to_ai():
    t, note, rest = apply_range(TEXT, "только про кислоты")
    assert t == TEXT and rest == "только про кислоты"


# --- деление большого текста ---
def test_split_and_allocate():
    big = "\n".join(f"## Раздел {i}\n" + ("Предложение о разделе номер %d. " % i) * 80 for i in range(10))
    chunks = split_text(big, 2000)
    assert len(chunks) > 1
    assert all(estimate_tokens(c) <= 2100 for c in chunks)
    assert "".join(chunks).replace("\n", "") == big.replace("\n", "")
    assert all(c.startswith("## Раздел") for c in chunks)  # режем по заголовкам
    assert sum(allocate(20, [len(c) for c in chunks])) == 20
    assert allocate(10, [100, 50, 50]) == [5, 3, 2]
    assert split_text("короткий", 1000) == ["короткий"]


def test_split_long_paragraph():
    long = "Очень длинное предложение без заголовков. " * 2000
    chunks = split_text(long, 1500)
    assert len(chunks) > 3 and all(len(c) <= 1500 * 3.2 + 50 for c in chunks)


# --- терпимый разбор JSON ---
def test_parse_json():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Вот ответ: {"a": [1, 2,],} спасибо') == {"a": [1, 2]}
    assert unwrap({"quiz": {"questions": []}}, ("questions",)) == {"questions": []}
    assert unwrap({"questions": [1]}, ("questions",)) == {"questions": [1]}
    assert to_number("0,5 балла") == 0.5 and to_number("1") == 1.0


def test_normalize_synonyms():
    q = normalize({"тип": "один вариант", "вопрос": "Что такое катод?", "варианты": ["A) плюс", "B) минус", "C) ноль", "D) ток"],
                   "правильный": "B", "цитата": "катод", "сложность": "сложный"})
    assert q["qtype"] == "single" and q["options"][1] == "минус" and q["answer"]["correct"] == [1]
    assert q["difficulty"] == "hard"
    q = normalize({"type": "multiple_choice", "question": "Какие?", "options": ["a", "b", "c", "d"], "correct": ["2", "4"]})
    assert q["answer"]["correct"] == [1, 3]  # «4» при 4 вариантах — значит номера с 1
    q = normalize({"type": "true_false", "text": "Утверждение тут.", "answer": "верно"})
    assert q["answer"]["value"] is True
    q = normalize({"type": "matching", "text": "Соотнеси термины", "pairs": {"а": "1", "б": "2", "в": "3"}})
    assert q["answer"]["pairs"] == [["а", "1"], ["б", "2"], ["в", "3"]]
    q = normalize({"type": "fill", "text": "Катод — это ____ электрод.", "answer": "отрицательный"})
    assert "___" in q["text"] and q["answer"]["accepted"] == ["отрицательный"]


def test_problems():
    quotes = QuoteIndex("На катоде идёт восстановление ионов металла из раствора соли.")
    good = normalize({"type": "single", "text": "Что идёт на катоде?", "options": ["восстановление", "окисление", "ничего", "горение"],
                      "correct": 0, "quote": "На катоде идёт восстановление ионов"})
    assert problems(good, quotes) == []
    bad = dict(good, quote="На катоде идёт окисление")
    assert problems(bad, quotes) == ["quote"]
    two = normalize({"type": "single", "text": "Что идёт на катоде?", "options": ["a", "b", "c", "d"], "correct": [0, 1],
                     "quote": "На катоде идёт восстановление ионов"})
    assert "нужен ровно 1 правильный вариант" in problems(two, quotes)
    assert is_duplicate("Что идёт на катоде при электролизе?", ["что идет на катоде при электролизе"])
    assert not is_duplicate("Что идёт на аноде?", ["Как называется прибор для измерения тока в цепи?"])


def test_type_plan():
    p = type_plan(10)
    assert sum(p.values()) == 10 and len(p) >= 5
    assert sum(type_plan(3).values()) == 3


def test_grade():
    assert grade("single", [], {"correct": [2]}, {"choice": 2}) == 1
    assert grade("multi", [], {"correct": [0, 2]}, {"choices": [0]}) == 0.5
    assert grade("multi", [], {"correct": [0, 2]}, {"choices": [0, 1]}) == 0
    assert grade("order", [], {"order": ["a", "b", "c"]}, {"order": ["a", "c", "b"]}) == round(1 / 3, 3)
    assert grade("match", [], {"pairs": [["a", "1"], ["b", "2"]]}, {"right": ["1", "2"]}) == 1
    assert grade("single", [], {"correct": [0]}, {"timeout": True}) == 0
    assert fill_ok("Электролиз", ["электролиз"]) and fill_ok("электролис", ["электролиз"])
    assert not fill_ok("ток", ["тег"])


def test_open_reference_anywhere():
    """Образец открытого ответа ИИ кладёт в разные поля — находим везде."""
    for raw in [{"type": "open", "text": "Объясни, что такое множество", "correct": ["набор", "объектов"]},
                {"type": "open", "text": "Объясни, что такое множество", "sample_answer": "Набор объектов"},
                {"type": "open", "text": "Объясни, что такое множество", "answer": {"text": "Набор объектов"}},
                {"type": "open", "text": "Объясни, что такое множество", "key_points": ["набор объектов"]},
                {"type": "open", "text": "Объясни, что такое множество", "explanation": "Это набор объектов."}]:
        q = normalize(raw)
        assert len(q["answer"]["reference"]) >= 5, raw
        assert "нет образца ответа" not in problems(q, None)
