"""Как делается квиз (всё, где участвует ИИ).

generate(quiz_id):
    1. берём сохранённый текст материала;
    2. если не влезает в один запрос — делим на куски, вопросы делим пропорционально;
    3. запрос `quiz` (по куску — один запрос), ИИ сам делит на темы и подтемы;
    4. код проверяет каждый вопрос (цитата есть в тексте, число правильных, дубли, общее число);
    5. все плохие вопросы и недостающие — одним запросом `replace`;
    6. сохраняем.
Для квиза по предмету: часть — старые вопросы предмета (сначала те, где ошибался), часть — новые от ИИ.
"""
from __future__ import annotations

import math
import random
import threading
from contextlib import contextmanager

import sqlalchemy as sa

from app import ai, config
from app.ai.base import AIError
from app.db import (OWNER_ID, Answer, Attempt, Question, Quiz, QuizText, SessionLocal, Subject, dumps, loads,
                    new_id, now)
from app.quiz import mockai, prompts
from app.quiz.material import (MaterialError, QuoteIndex, allocate, estimate_tokens, nice_subject, norm_for_match,
                               norm_subject, split_text)
from app.quiz.questions import TYPES, finalize, is_duplicate, normalize, problems, public, snapshot, type_plan

TEXT_PART = 200_000          # символов в одной строке quiz_texts (YDB любит небольшие ячейки)
TOKENS_PER_QUESTION = 380    # сколько токенов ответа закладываем на один вопрос
PROMPT_OVERHEAD = 2200       # системные правила + инструкции + схема


class QuizError(RuntimeError):
    """Понятная ошибка для человека."""


# --- Предметы и тексты --------------------------------------------------------------

def get_or_create_subject(name: str, user_id: str = OWNER_ID) -> Subject:
    norm = norm_subject(name)
    if not norm:
        raise QuizError("Впиши предмет")
    with SessionLocal() as s:
        subj = s.scalars(sa.select(Subject).where(Subject.user_id == user_id, Subject.norm == norm)).first()
        if subj:
            return subj
        subj = Subject(user_id=user_id, name=nice_subject(name), norm=norm)
        s.add(subj)
        s.commit()
        return subj


def save_text(quiz_id: str, text: str, user_id: str = OWNER_ID) -> None:
    with SessionLocal() as s:
        s.execute(sa.delete(QuizText).where(QuizText.quiz_id == quiz_id))
        for i in range(0, max(1, len(text)), TEXT_PART):
            s.add(QuizText(user_id=user_id, quiz_id=quiz_id, part=i // TEXT_PART, text=text[i:i + TEXT_PART]))
        s.commit()


def load_text(quiz_id: str) -> str:
    with SessionLocal() as s:
        rows = s.scalars(sa.select(QuizText).where(QuizText.quiz_id == quiz_id).order_by(QuizText.part)).all()
    return "".join(r.text for r in rows)


def subject_material(subject_id: str) -> str:
    """Все сохранённые тексты обычных квизов предмета (чем больше квизов — тем больше материала)."""
    with SessionLocal() as s:
        quizzes = s.scalars(sa.select(Quiz).where(Quiz.subject_id == subject_id, Quiz.kind == "normal")
                            .order_by(Quiz.created_at)).all()
    parts = []
    for q in quizzes:
        t = load_text(q.id)
        if t:
            parts.append(f"## Квиз «{q.topic}»\n{t}")
    return "\n\n".join(parts)


def quiz_material(quiz: Quiz) -> str:
    return subject_material(quiz.subject_id) if quiz.kind == "subject" else load_text(quiz.id)


# --- Статус задачи ------------------------------------------------------------------

def set_status(quiz_id: str, status: str | None = None, text: str | None = None, error: str | None = None) -> None:
    with SessionLocal() as s:
        q = s.get(Quiz, quiz_id)
        if q is None:
            return
        if status:
            q.status = status
        if text is not None:
            q.status_text = text
        if error is not None:
            q.error = error
        q.heartbeat_at = now()
        s.commit()


@contextmanager
def heartbeat(quiz_id: str):
    """Пока идёт работа, раз в 20 секунд отмечаемся в базе. Если сервер упадёт, отметки прекратятся,
    и через JOB_STALE_SECONDS экран покажет ошибку с кнопкой «Повторить»."""
    stop = threading.Event()

    def beat():
        while not stop.wait(20):
            try:
                set_status(quiz_id)
            except Exception:
                pass

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        yield
    finally:
        stop.set()


def check_stale(quiz: Quiz) -> Quiz:
    """Задача «зависла» (сервер перезапустился посреди работы)? Тогда помечаем ошибкой."""
    if quiz.status == "running" and quiz.heartbeat_at is not None:
        age = (now() - quiz.heartbeat_at).total_seconds()
        if age > config.JOB_STALE_SECONDS:
            msg = "Сервер перезапускался во время создания квиза. Нажми «Повторить»."
            set_status(quiz.id, "error", msg, msg)
            quiz.status, quiz.status_text, quiz.error = "error", msg, msg
    return quiz


# --- Создание вопросов ----------------------------------------------------------------

def _limits(p) -> tuple[int, int]:
    """(сколько вопросов влезает в один ответ, сколько токенов материала в один запрос)"""
    per_request = max(3, (p.max_output - 600) // TOKENS_PER_QUESTION)
    return per_request, max(2000, p.context_tokens - PROMPT_OVERHEAD - p.max_output)


def plan_requests(material: str, n: int, p=None) -> list[tuple[str, int]]:
    """Разбиваем работу на запросы: [(кусок текста, сколько вопросов)]."""
    p = p or ai.get_provider()
    per_request, text_budget = _limits(p)
    text_budget = min(text_budget, 60000)  # даже у больших моделей не шлём больше — дорого
    chunks = split_text(material, text_budget)
    need = max(len(chunks), math.ceil(n / per_request))
    if len(chunks) < need and estimate_tokens(material) > 1500:
        chunks = split_text(material, max(1000, estimate_tokens(material) // need + 1))
    counts = allocate(n, [len(c) for c in chunks])
    # если кусков меньше, чем нужно запросов (маленький текст, много вопросов) — дробим число вопросов
    jobs: list[tuple[str, int]] = []
    for c, k in zip(chunks, counts):
        while k > 0:
            take = min(k, per_request)
            jobs.append((c, take))
            k -= take
    return jobs


def is_big(material: str, n: int) -> bool:
    try:
        return len(plan_requests(material, n)) > 1
    except AIError:
        return False


def _excerpt(material: str, near: list[str], budget_tokens: int) -> str:
    """Кусок материала для замены вопроса: весь, если влезает, иначе кусок с нужной цитатой."""
    if estimate_tokens(material) <= budget_tokens:
        return material
    chunks = split_text(material, budget_tokens)
    for quote in near:
        nq = norm_for_match(quote)[:80]
        if not nq:
            continue
        for c in chunks:
            if nq in norm_for_match(c):
                return c
    return chunks[0]


def _replace_round(quiz: Quiz, slots: list[dict], material: str, p, hints: bool, subject: str,
                   status_cb=None) -> dict[str, dict]:
    """Один запрос `replace` на все слоты (если материал большой — по запросу на кусок)."""
    if not slots:
        return {}
    _, budget = _limits(p)
    budget = min(budget, 60000)
    groups: dict[str, list[dict]] = {}
    for s in slots:
        ex = _excerpt(material, [s.get("near_quote", "")], budget)
        groups.setdefault(ex, []).append(s)
    out: dict[str, dict] = {}
    for ex, group in groups.items():
        if status_cb:
            status_cb()
        data = ai.ask_json(
            "replace", prompts.replace_prompt(slots=group, material=ex, hints=hints, subject=subject, title=quiz.topic),
            expect=("questions",), quiz_id=quiz.id, quiz_title=quiz.topic, user_id=quiz.user_id,
            max_tokens=min(p.max_output, 600 + TOKENS_PER_QUESTION * len(group)),
            mock=lambda ex=ex, group=group: mockai.replace(ex, group))
        items = data.get("questions") if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = [items]
        for i, raw in enumerate(items or []):
            if not isinstance(raw, dict):
                continue
            slot = str(raw.get("slot") or (group[i]["slot"] if i < len(group) else ""))
            sd = next((g for g in group if g["slot"] == slot), None)
            if sd is None:
                continue
            any_type = sd["type"] == "any"
            q = normalize(raw, {"topic": sd.get("topic", ""), "subtopic": sd.get("subtopic", ""),
                                "qtype": "" if any_type else sd["type"], "difficulty": sd.get("difficulty", "medium")})
            if any_type:
                if q["qtype"] == sd.get("not_type"):
                    q["_wrong_type"] = True
            elif q["qtype"] != sd["type"] and sd["type"] in TYPES:
                q["_wrong_type"] = True
            out[slot] = q
    return out


def make_questions(quiz: Quiz, material: str, n: int, difficulty: str, hints: bool, subject: str,
                   instruction: str = "", avoid: list[str] | None = None, status_cb=None,
                   known_topics: list[str] | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """Сделать n проверенных вопросов по материалу. → (темы, вопросы, предупреждения)."""
    p = ai.get_provider()
    jobs = plan_requests(material, n, p)
    quotes = QuoteIndex(material)
    topics: list[dict] = []
    topic_names: dict[str, str] = {}
    good: list[dict] = []
    bad: list[dict] = []
    warnings: list[str] = []
    avoid = list(avoid or [])
    say = status_cb or (lambda *_: None)

    def add_topics(items):
        for t in items or []:
            if isinstance(t, str):
                t = {"name": t, "subtopics": []}
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or t.get("topic") or t.get("тема") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key not in topic_names:
                topic_names[key] = name
                topics.append({"name": name, "subtopics": []})
            entry = next(x for x in topics if x["name"].lower() == key)
            subs = t.get("subtopics") or t.get("подтемы") or []
            for sname in subs if isinstance(subs, list) else []:
                sname = str(sname.get("name") if isinstance(sname, dict) else sname).strip()
                if sname and sname.lower() not in [x.lower() for x in entry["subtopics"]]:
                    entry["subtopics"].append(sname)

    for k, (chunk, cnt) in enumerate(jobs, start=1):
        say("делаю вопросы…" if len(jobs) == 1 else f"делаю вопросы… (часть {k} из {len(jobs)})")
        plan = type_plan(cnt)
        prev = avoid + [q["text"] for q in good]
        data = ai.ask_json(
            "subject_quiz" if quiz.kind == "subject" else "quiz",
            prompts.quiz_prompt(title=quiz.topic, subject=subject, n=cnt, difficulty=difficulty, plan=plan,
                                hints=hints, material=chunk, instruction=instruction,
                                part=(k, len(jobs)) if len(jobs) > 1 else None,
                                known_topics=[t["name"] for t in topics] or known_topics,
                                avoid=prev if (prev and (len(jobs) > 1 or avoid)) else None),
            expect=("questions",), quiz_id=quiz.id, quiz_title=quiz.topic, user_id=quiz.user_id,
            max_tokens=min(p.max_output, 600 + TOKENS_PER_QUESTION * cnt),
            mock=lambda chunk=chunk, cnt=cnt, plan=plan, k=k, prev=prev: mockai.quiz(chunk, cnt, plan, offset=k * 31, avoid=prev))
        if isinstance(data, list):
            data = {"questions": data}
        add_topics(data.get("topics") or data.get("темы"))
        raw_qs = data.get("questions") or data.get("вопросы") or []
        for raw in raw_qs[:cnt + 3]:
            q = normalize(raw, {"difficulty": difficulty})
            q["difficulty"] = difficulty
            q["_chunk_plan"] = plan
            probs = problems(q, quotes)
            dup = is_duplicate(q.get("text", ""), avoid + [g["text"] for g in good])
            if not probs and not dup:
                good.append(q)
            else:
                q["_problems"] = probs + (["дубль"] if dup else [])
                bad.append(q)

    say("проверяю…")
    if len(good) > n:
        good = good[:n]
    # Слоты на замену: плохие вопросы + недостающие до n.
    slots: list[dict] = []
    need = n - len(good)
    plan_all = type_plan(n)
    have = {t: sum(1 for g in good if g["qtype"] == t) for t in TYPES}
    for q in bad[:need]:
        t = q["qtype"] if q["qtype"] in TYPES else next((t for t, c in plan_all.items() if have.get(t, 0) < c), "single")
        have[t] = have.get(t, 0) + 1
        slots.append({"slot": f"s{len(slots) + 1}", "type": t, "topic": q.get("topic", ""), "subtopic": q.get("subtopic", ""),
                      "difficulty": difficulty, "flag": q.get("flag", ""), "near_quote": q.get("quote", ""),
                      "avoid": [q.get("text", "")] + [g["text"] for g in good if g.get("topic") == q.get("topic")][:6]})
    names = [t["name"] for t in topics] or ["Основное"]
    while len(slots) < need:
        t = next((t for t, c in plan_all.items() if have.get(t, 0) < c), "single")
        have[t] = have.get(t, 0) + 1
        top = names[len(slots) % len(names)]
        slots.append({"slot": f"s{len(slots) + 1}", "type": t, "topic": top, "subtopic": "", "difficulty": difficulty,
                      "flag": "", "near_quote": "", "avoid": [g["text"] for g in good][:10]})
    quote_only: list[dict] = []
    if slots:
        say("заменяю неудачные вопросы…")
        repl = _replace_round(quiz, slots, material, p, hints, subject, status_cb=None)
        for s_ in slots:
            q = repl.get(s_["slot"])
            if not q:
                continue
            q["difficulty"] = difficulty
            probs = problems(q, quotes)
            dup = is_duplicate(q.get("text", ""), avoid + [g["text"] for g in good])
            if not probs and not dup:
                good.append(q)
            elif probs == ["quote"] and not dup:
                quote_only.append(q)
        # Не хватает — берём вопросы, у которых всё хорошо, кроме цитаты (цитату не показываем).
        for q in bad + quote_only:
            if len(good) >= n:
                break
            if q.get("_problems", problems(q, quotes)) in (["quote"],) or (q in quote_only):
                if not is_duplicate(q["text"], [g["text"] for g in good]):
                    q["quote"] = ""
                    q["_no_quote"] = True
                    good.append(q)
    no_quote = sum(1 for q in good if q.get("_no_quote"))
    if no_quote:
        warnings.append(f"У {no_quote} вопр. ИИ не дал точную цитату из материала — проверь их в итоге.")
    if len(good) < n:
        warnings.append(f"Получилось {len(good)} вопросов из {n}: ИИ не смог сделать остальные без ошибок.")
    if not good:
        raise QuizError("ИИ не смог сделать ни одного правильного вопроса. Нажми «Повторить» или поменяй материал.")
    # Темы вопросов, которых нет в списке тем, — добавляем.
    for q in good:
        if not q.get("topic"):
            q["topic"] = names[0]
        add_topics([{"name": q["topic"], "subtopics": [q["subtopic"]] if q.get("subtopic") else []}])
        q["topic"] = topic_names.get(q["topic"].lower(), q["topic"])
        finalize(q, hints)
    return topics, good[:n], warnings


def _save_questions(quiz_id: str, user_id: str, qs: list[dict], start: int = 0) -> None:
    with SessionLocal() as s:
        for i, q in enumerate(qs):
            s.add(Question(user_id=user_id, quiz_id=quiz_id, position=start + i, topic=q.get("topic"),
                           subtopic=q.get("subtopic") or "", qtype=q["qtype"], difficulty=q.get("difficulty"),
                           flag=q.get("flag") or "", text=q["text"], options=dumps(q.get("options")),
                           answer=dumps(q.get("answer")), explanation=q.get("explanation") or "",
                           quote=q.get("quote") or "", points=q.get("points", 1), hint=q.get("hint") or "",
                           replace_count=0, replace_history=dumps([]),
                           source_question_id=q.get("source_question_id")))
        s.commit()


def generate(quiz_id: str) -> dict:
    """Сделать вопросы квиза. Идёт внутри HTTP-запроса; экран тем временем спрашивает статус."""
    with SessionLocal() as s:
        quiz = s.get(Quiz, quiz_id)
        if quiz is None:
            raise QuizError("Квиз не найден")
        quiz = check_stale(quiz)
        if quiz.status == "running":
            raise QuizError("Квиз уже делается. Подожди.")
        if quiz.status == "ready":
            return {"status": "ready"}
        subj = s.get(Subject, quiz.subject_id)
        s.execute(sa.delete(Question).where(Question.quiz_id == quiz_id))  # если это «Повторить»
        s.commit()
    params = loads(quiz.params, {})
    n = int(params.get("count", 10))
    difficulty = params.get("difficulty", "medium")
    hints = bool(params.get("hints"))
    set_status(quiz_id, "running", "готовлюсь…", "")
    try:
        with heartbeat(quiz_id):
            say = lambda text: set_status(quiz_id, text=text)  # noqa: E731
            warnings = loads(quiz.warnings, [])
            if quiz.kind == "subject":
                topics, qs, w = _subject_questions(quiz, subj, params, n, difficulty, hints, say)
            else:
                material = load_text(quiz_id)
                if not material:
                    raise QuizError("Текст материала не сохранился. Создай квиз заново.")
                topics, qs, w = make_questions(quiz, material, n, difficulty, hints, subj.name if subj else "",
                                               instruction=params.get("range_rest", ""), status_cb=say)
            warnings += w
            _save_questions(quiz_id, quiz.user_id, qs)
            with SessionLocal() as s:
                qz = s.get(Quiz, quiz_id)
                qz.topics = dumps(topics)
                qz.warnings = dumps(list(dict.fromkeys(warnings)))
                qz.status, qz.status_text, qz.error = "ready", "готово", ""
                qz.heartbeat_at = now()
                s.commit()
        return {"status": "ready", "warnings": warnings}
    except (AIError, QuizError, MaterialError) as e:
        set_status(quiz_id, "error", f"ошибка: {e}", str(e))
        raise
    except Exception as e:
        msg = f"Неожиданная ошибка: {type(e).__name__}: {e}"
        set_status(quiz_id, "error", msg, msg)
        raise QuizError(msg) from e


# --- Квиз по предмету -----------------------------------------------------------------

def _pool(subject_id: str) -> list[Question]:
    with SessionLocal() as s:
        quiz_ids = [q.id for q in s.scalars(sa.select(Quiz).where(
            Quiz.subject_id == subject_id, Quiz.kind == "normal", Quiz.status == "ready")).all()]
        if not quiz_ids:
            return []
        return list(s.scalars(sa.select(Question).where(Question.quiz_id.in_(quiz_ids))).all())


def _last_results(question_ids: list[str]) -> dict[str, bool]:
    """Последний результат по каждому вопросу (учитываем и ответы на его копии в квизах по предмету)."""
    if not question_ids:
        return {}
    with SessionLocal() as s:
        copies = s.execute(sa.select(Question.id, Question.source_question_id)
                           .where(Question.source_question_id.in_(question_ids))).all()
        src = {cid: sid for cid, sid in copies}
        ids = list(question_ids) + list(src)
        rows = s.execute(sa.select(Answer.question_id, Answer.correct, Answer.created_at)
                         .where(Answer.question_id.in_(ids))).all()
    last: dict[str, tuple] = {}
    for qid, correct, at in rows:
        key = src.get(qid, qid)
        if key not in last or (at and last[key][1] and at > last[key][1]):
            last[key] = (bool(correct), at)
    return {k: v[0] for k, v in last.items()}


def pick_old(subject_id: str, count: int, difficulty: str) -> list[str]:
    """Старые вопросы для квиза по предмету: сначала где ошибался, потом новые для тебя, потом остальные."""
    pool = _pool(subject_id)
    res = _last_results([q.id for q in pool])
    rnd = random.Random()

    def prio(q: Question):
        r = res.get(q.id)
        return (0 if r is False else 1 if r is None else 2, 0 if q.difficulty == difficulty else 1, rnd.random())

    return [q.id for q in sorted(pool, key=prio)[:count]]


def create_subject_quiz(subject_id: str, params: dict, user_id: str = OWNER_ID) -> str:
    with SessionLocal() as s:
        subj = s.get(Subject, subject_id)
        if subj is None:
            raise QuizError("Предмет не найден")
        has = s.scalar(sa.select(sa.func.count()).select_from(Quiz).where(
            Quiz.subject_id == subject_id, Quiz.kind == "normal", Quiz.status == "ready")) or 0
    if not has:
        raise QuizError("В предмете пока нет готовых квизов — сначала создай обычный квиз.")
    n = int(params.get("count", 10))
    pool_size = len(_pool(subject_id))
    n_old = min(pool_size, round(n * config.SUBJECT_OLD_SHARE))
    old_ids = pick_old(subject_id, n_old, params.get("difficulty", "medium"))
    params = dict(params, old_ids=old_ids)
    qid = new_id()
    with SessionLocal() as s:
        s.add(Quiz(id=qid, user_id=user_id, subject_id=subject_id, kind="subject",
                   topic=f"{subj.name}: квиз по предмету", params=dumps(params), status="new",
                   status_text="жду запуска", sources=dumps([]), warnings=dumps([])))
        s.commit()
    return qid


def _subject_questions(quiz: Quiz, subj: Subject, params: dict, n: int, difficulty: str, hints: bool, say):
    say("беру старые вопросы…")
    old_ids = params.get("old_ids") or []
    with SessionLocal() as s:
        olds = list(s.scalars(sa.select(Question).where(Question.id.in_(old_ids))).all()) if old_ids else []
    olds.sort(key=lambda q: old_ids.index(q.id))
    qs: list[dict] = []
    for o in olds:
        qs.append({"topic": o.topic, "subtopic": o.subtopic, "qtype": o.qtype, "difficulty": o.difficulty,
                   "flag": o.flag, "text": o.text, "options": loads(o.options), "answer": loads(o.answer),
                   "explanation": o.explanation, "quote": o.quote, "points": o.points,
                   "hint": o.hint if hints else "", "source_question_id": o.id})
    topics: list[dict] = []
    for o in olds:
        t = next((x for x in topics if x["name"] == o.topic), None)
        if t is None:
            t = {"name": o.topic, "subtopics": []}
            topics.append(t)
        if o.subtopic and o.subtopic not in t["subtopics"]:
            t["subtopics"].append(o.subtopic)
    warnings: list[str] = []
    n_new = n - len(qs)
    if n_new > 0:
        material = subject_material(subj.id)
        if not material:
            raise QuizError("У предмета нет сохранённых текстов.")
        p = ai.get_provider()
        _, budget = _limits(p)
        budget = min(budget, 60000)
        chunks = split_text(material, budget)
        if len(chunks) > 1:
            # Берём столько кусков, сколько нужно на новые вопросы, из разных мест материала.
            per_request, _ = _limits(p)
            k = min(len(chunks), max(1, math.ceil(n_new / per_request)))
            start = random.randrange(len(chunks))
            step = len(chunks) / k
            material = "\n\n".join(chunks[int(start + i * step) % len(chunks)] for i in range(k))
        pool_texts = [q.text for q in _pool(subj.id)]
        new_topics, new_qs, warnings = make_questions(
            quiz, material, n_new, difficulty, hints, subj.name, avoid=pool_texts, status_cb=say,
            known_topics=[t["name"] for t in topics])
        for t in new_topics:
            ex = next((x for x in topics if x["name"].lower() == t["name"].lower()), None)
            if ex is None:
                topics.append(t)
            else:
                ex["subtopics"] += [s for s in t["subtopics"] if s not in ex["subtopics"]]
        qs += new_qs
    random.shuffle(qs)
    return topics, qs, warnings


# --- Замена одного вопроса ----------------------------------------------------------

def replace_question(question_id: str) -> dict:
    """Кнопка «заменить вопрос». Новый вопрос — той же темы и сложности, но ДРУГОГО вида.
    Если ИИ прислал неудачный вопрос — одна повторная попытка, потом честное сообщение.
    → {"changed": bool, "message": str, "question": {...}}"""
    with SessionLocal() as s:
        q = s.get(Question, question_id)
        if q is None:
            raise QuizError("Вопрос не найден")
        quiz = s.get(Quiz, q.quiz_id)
        subj = s.get(Subject, quiz.subject_id) if quiz else None
        others = [o.text for o in s.scalars(sa.select(Question).where(Question.quiz_id == q.quiz_id,
                                                                      Question.id != q.id)).all()]
    if (q.replace_count or 0) >= config.MAX_REPLACES:
        return {"changed": False, "message": f"Этот вопрос уже заменён {config.MAX_REPLACES} раза — больше нельзя.",
                "question": public(q)}
    history = loads(q.replace_history, [])
    material = quiz_material(quiz)
    if not material:
        raise QuizError("Текст материала не найден — заменить вопрос не из чего.")
    params = loads(quiz.params, {})
    hints = bool(params.get("hints"))
    p = ai.get_provider()
    quotes = QuoteIndex(material)
    avoid = [h.get("text", "") for h in history] + [q.text]
    reason = "ИИ не прислал новый вопрос"
    got = None
    for _attempt in range(2):
        slot = {"slot": "s1", "type": "any", "not_type": q.qtype, "topic": q.topic, "subtopic": q.subtopic,
                "difficulty": q.difficulty, "flag": q.flag or "", "near_quote": q.quote or "",
                "avoid": avoid + others[:20]}
        cand = _replace_round(quiz, [slot], material, p, hints, subj.name if subj else "").get("s1")
        if not cand:
            reason = "ИИ не прислал новый вопрос"
            continue
        if is_duplicate(cand.get("text", ""), avoid):
            reason = "ИИ прислал такой же вопрос, как раньше"
            continue
        if is_duplicate(cand.get("text", ""), others):
            reason = "ИИ прислал вопрос, который уже есть в квизе"
            continue
        probs = problems(cand, quotes)
        if cand.get("_wrong_type"):
            probs.append("вид вопроса не поменялся")
        if probs:
            reason = "новый вопрос не прошёл проверку (" + ", ".join(
                "цитаты нет в материале" if x == "quote" else x for x in probs) + ")"
            continue
        got = cand
        break
    if got is None:
        return {"changed": False, "message": f"{reason[:1].upper()}{reason[1:]}. Замена не засчитана — попробуй ещё раз.",
                "question": public(q)}
    got["difficulty"] = q.difficulty
    finalize(got, hints)
    with SessionLocal() as s:
        q = s.get(Question, question_id)
        history.append(snapshot(q))
        q.qtype = got["qtype"]
        q.text, q.options, q.answer = got["text"], dumps(got["options"]), dumps(got["answer"])
        q.explanation, q.quote, q.hint = got.get("explanation", ""), got.get("quote", ""), got.get("hint", "")
        q.subtopic = got.get("subtopic") or q.subtopic
        q.flag = got.get("flag") or ""
        if not hints or q.flag not in ("outside", "logic"):
            q.hint = ""
        q.points = got["points"]
        q.replace_count = (q.replace_count or 0) + 1
        q.replace_history = dumps(history)
        s.commit()
        left = config.MAX_REPLACES - q.replace_count
        return {"changed": True, "message": f"Вопрос заменён. Осталось замен: {left}.", "question": public(q)}


# --- Проверка открытого ответа ------------------------------------------------------

def check_open(quiz: Quiz, question: Question, text: str) -> tuple[float, str]:
    """→ (доля 0 / 0.5 / 1, комментарий). Бросает AIError — тогда экран спросит «засчитать?»."""
    ans = loads(question.answer, {})
    if not text.strip():
        return 0.0, "Ответ пустой."
    data = ai.ask_json(
        "check_open", prompts.check_open_prompt(question=question.text, reference=ans.get("reference", ""),
                                                key_points=ans.get("key_points", []), answer=text[:3000]),
        expect=("score",), quiz_id=quiz.id, quiz_title=quiz.topic, user_id=quiz.user_id, max_tokens=300,
        mock=lambda: mockai.check_open(ans.get("reference", ""), text))
    raw = data.get("score", data.get("баллы", data.get("оценка"))) if isinstance(data, dict) else None
    verdict = str(data.get("verdict", "")).lower() if isinstance(data, dict) else ""
    score = ai.to_number(raw, -1)
    if score < 0:
        score = 1.0 if "full" in verdict or "полност" in verdict else 0.5 if "part" in verdict or "частич" in verdict else -1
    if score < 0:
        raise AIError("ИИ не поставил оценку.", "format")
    if score > 1:  # прислал в баллах из 2 или в процентах
        score = score / 100 if score > 2 else score / 2
    score = 1.0 if score >= 0.75 else 0.5 if score >= 0.25 else 0.0
    comment = str(data.get("comment") or data.get("explanation") or data.get("комментарий") or "").strip()
    return score, comment


# --- Удаление -------------------------------------------------------------------------

def delete_quiz(quiz_id: str) -> None:
    """Стираем квиз, его текст, попытки, ответы и вопросы (они же уходят из пула предмета).
    Расходы остаются в статистике (там сохранено название квиза)."""
    with SessionLocal() as s:
        quiz = s.get(Quiz, quiz_id)
        if quiz is None:
            raise QuizError("Квиз не найден")
        subject_id = quiz.subject_id
        s.execute(sa.delete(Answer).where(Answer.quiz_id == quiz_id))
        s.execute(sa.delete(Attempt).where(Attempt.quiz_id == quiz_id))
        s.execute(sa.delete(Question).where(Question.quiz_id == quiz_id))
        s.execute(sa.delete(QuizText).where(QuizText.quiz_id == quiz_id))
        s.delete(quiz)
        s.commit()
        left = s.scalar(sa.select(sa.func.count()).select_from(Quiz).where(Quiz.subject_id == subject_id)) or 0
        if not left:
            subj = s.get(Subject, subject_id)
            if subj:
                s.delete(subj)
                s.commit()
