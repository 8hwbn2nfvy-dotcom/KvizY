"""Материал для квиза: текст из файлов и ссылок, чистка, обрезка «откуда и докуда».

Файлы НЕ хранятся — только извлечённый текст. В тексте ставятся метки, по которым потом
режем и делим на куски:
    [Файл 2: лекция.pdf]
    [Страница 5]       — в PDF
    [Слайд 7]          — в .pptx
    ## Заголовок       — заголовки в Word и на сайтах
"""
from __future__ import annotations

import io
import re
import unicodedata

from app import config

ALLOWED = (".pdf", ".docx", ".pptx", ".txt", ".md")


class MaterialError(ValueError):
    """Понятная ошибка про материал (показывается человеку)."""


# --- Чистка текста ------------------------------------------------------------------

# Латинские буквы-двойники, которые PDF иногда подставляет вместо русских.
_HOMO = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "x": "х", "y": "у", "k": "к",
    "A": "А", "B": "В", "E": "Е", "K": "К", "M": "М", "H": "Н", "O": "О", "P": "Р",
    "C": "С", "T": "Т", "X": "Х", "ĸ": "к", "ᴋ": "к", "ᴍ": "м", "ʜ": "н", "ᴛ": "т", "ʙ": "в",
    "ə": "е", "ɑ": "а", "ο": "о", "α": "а", "ε": "е", "κ": "к", "ρ": "р", "τ": "т", "υ": "у", "χ": "х",
})
_LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_CYR = re.compile(r"[а-яёА-ЯЁ]")
_WORD = re.compile(r"[^\W\d_]+", re.U)


def fix_homoglyphs(text: str) -> str:
    """В словах, где есть русские буквы, латинские двойники меняем на русские
    («ĸлетка» → «клетка», «мoлекула» → «молекула»). Чисто латинские слова не трогаем."""
    text = text.replace("ĸ", "к")

    def repl(m: re.Match) -> str:
        w = m.group(0)
        if _CYR.search(w) and re.search(r"[A-Za-zĸᴋᴍʜᴛʙəɑοαεκρτυχ]", w):
            return w.translate(_HOMO)
        return w

    return _WORD.sub(repl, text)


# Настоящие маркеры списка: «- », «• », «* », «1. », «2) », «а) ». Годы и числа не трогаем.
_MARKER = re.compile(r"^\s*(?:[-–—•●○◦▪■□►▸*·]\s+|\d{1,2}[.)]\s+(?=\S)|[a-zа-я][)]\s+)", re.I)


def strip_list_marker(line: str) -> str:
    """Убираем маркер списка в начале строки.
    «1. Пункт» → «Пункт», «2) Пункт» → «Пункт», «- Пункт» → «Пункт».
    «1945 год» и «1945. Конец войны» и «3.14 — число» остаются как есть."""
    return _MARKER.sub("", line, count=1)


def clean_text(text: str) -> str:
    """Чистим извлечённый текст: странные пробелы, переносы, буквы-двойники, маркеры списков."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    for k, v in _LIGATURES.items():
        text = text.replace(k, v)
    text = text.replace("\xad", "").replace("​", "").replace("﻿", "")
    text = re.sub(r"[   \t]+", " ", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = fix_homoglyphs(text)
    # перенос слова по слогам в конце строки: «элек-\nтролиз» → «электролиз»
    text = re.sub(r"([а-яёa-z])-\n([а-яёa-z])", r"\1\2", text)
    lines = []
    for line in text.split("\n"):
        line = re.sub(r" {2,}", " ", line).strip()
        if line.startswith(("[Файл", "[Страница", "[Слайд", "## ")):
            lines.append(line)
            continue
        lines.append(strip_list_marker(line))
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def join_parts(*parts: str, sep: str = " ") -> str:
    """Склеить куски фразы: пустые пропускаем, точки не удваиваем («Готово.. » не бывает)."""
    out = ""
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        if out and out.endswith((".", "!", "?", "…")) and p.startswith("."):
            p = p.lstrip(".").strip()
            if not p:
                continue
        out = f"{out}{sep}{p}" if out else p
    return re.sub(r"\.{2}(?!\.)", ".", out)


# --- Извлечение -----------------------------------------------------------------------

def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    if not data.startswith(b"%PDF"):
        raise MaterialError("файл не похож на PDF")
    reader = PdfReader(io.BytesIO(data))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        t = page.extract_text() or ""
        # строки PDF рвутся посреди предложения — склеиваем, если строка не закончена
        t = re.sub(r"(?<![.!?:;])\n(?=[a-zа-яё(«\"])", " ", t)
        parts.append(f"[Страница {i}]\n{t.strip()}")
    return "\n\n".join(parts)


def _docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    out = []
    body = doc.element.body
    # идём по документу по порядку: абзацы и таблицы
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            t = p.text.strip()
            if not t:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            if style.lower().startswith(("heading", "заголовок", "title")):
                out.append(f"## {t}")
            else:
                out.append(t)
        elif tag == "tbl":
            for row in Table(child, doc).rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    out.append(" | ".join(dict.fromkeys(cells)))
    return "\n".join(out)


def _pptx(data: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    parts = []

    def shape_texts(shapes):
        for sh in shapes:
            if getattr(sh, "shape_type", None) == 6 and hasattr(sh, "shapes"):  # группа фигур
                yield from shape_texts(sh.shapes)
            elif getattr(sh, "has_text_frame", False) and sh.text_frame.text.strip():
                yield sh.text_frame.text.strip()
            elif getattr(sh, "has_table", False):
                for row in sh.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        yield " | ".join(cells)

    for i, slide in enumerate(prs.slides, start=1):
        texts = list(shape_texts(slide.shapes))
        notes = ""
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
        block = [f"[Слайд {i}]"] + texts + ([f"Заметки: {notes}"] if notes else [])
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def _txt(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore")


def extract_file(filename: str, data: bytes) -> str:
    """Текст из одного файла. Бросает MaterialError с понятным текстом."""
    name = (filename or "").lower()
    if len(data) > config.MAX_FILE_MB * 1024 * 1024:
        raise MaterialError(f"«{filename}» больше {config.MAX_FILE_MB} МБ")
    if name.endswith((".doc", ".ppt")):
        raise MaterialError(f"«{filename}»: старый формат. Сохрани как .docx / .pptx и загрузи снова")
    if not name.endswith(ALLOWED):
        raise MaterialError(f"«{filename}»: такой тип не поддерживается. Можно PDF, Word (.docx), PowerPoint (.pptx), .txt")
    if name.endswith((".docx", ".pptx")) and not data.startswith(b"PK"):
        raise MaterialError(f"«{filename}» не открывается: файл повреждён или это не {name.rsplit('.', 1)[-1]}")
    try:
        if name.endswith(".pdf"):
            text = _pdf(data)
        elif name.endswith(".docx"):
            text = _docx(data)
        elif name.endswith(".pptx"):
            text = _pptx(data)
        else:
            text = _txt(data)
    except MaterialError as e:
        raise MaterialError(f"«{filename}»: {e}") from e
    except Exception as e:  # битый файл
        raise MaterialError(f"«{filename}» не открывается ({type(e).__name__}). Возможно, файл повреждён.") from e
    text = clean_text(text)
    plain = re.sub(r"\[(Страница|Слайд) \d+\]", "", text).strip()
    if len(plain) < 20:
        hint = " Похоже, это скан или картинки — текст из фото пока не распознаём." if name.endswith(".pdf") else ""
        raise MaterialError(f"«{filename}»: в файле почти нет текста.{hint}")
    return text


def fetch_url(url: str) -> tuple[str, str]:
    """Текст со страницы сайта → (заголовок, текст)."""
    import httpx
    from bs4 import BeautifulSoup

    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    headers = {"User-Agent": "KvizY/1.0 (personal study app; text extraction)",
               "Accept-Language": "ru,en;q=0.8"}
    try:
        with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as c:
            r = c.get(url)
    except httpx.HTTPError as e:
        raise MaterialError(f"Ссылка {url} не открылась: {type(e).__name__}") from e
    if r.status_code != 200:
        raise MaterialError(f"Ссылка {url} не открылась: сайт ответил {r.status_code}")
    ctype = r.headers.get("content-type", "")
    if "pdf" in ctype:
        return url, extract_file("page.pdf", r.content)
    if "html" not in ctype and "text" not in ctype:
        raise MaterialError(f"По ссылке {url} не страница, а «{ctype}»")
    soup = BeautifulSoup(r.text[:3_000_000], "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else url)[:200]
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    # Если article/main нет — берём блок, где больше всего текста в абзацах.
    if root is soup.body:
        best, best_len = root, 0
        for div in soup.find_all(["div", "section"]):
            ln = sum(len(p.get_text(" ", strip=True)) for p in div.find_all("p", recursive=False))
            if ln > best_len:
                best, best_len = div, ln
        if best_len > 500:
            root = best
    lines = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "blockquote", "td"]):
        t = el.get_text(" ", strip=True)
        if not t:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            lines.append(f"## {t}")
        elif el.name == "li":
            lines.append(t)
        else:
            lines.append(t)
    text = clean_text("\n".join(dict.fromkeys(lines)))
    if len(text) < 100:
        raise MaterialError(f"На странице {url} почти нет текста (может, сайт показывает текст только через скрипты)")
    return title, text


def combine(sources: list[tuple[str, str]]) -> str:
    """Склеить несколько источников с метками «[Файл N: имя]»."""
    return "\n\n".join(f"[Файл {i}: {name}]\n{text}" for i, (name, text) in enumerate(sources, start=1))


# --- «Откуда и докуда читать» -------------------------------------------------------

_ORD = {"перв": 1, "втор": 2, "трет": 3, "четв": 4, "пят": 5, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}


def _num(token: str) -> int | None:
    token = token.lower()
    for k, v in _ORD.items():
        if token.startswith(k):
            return v
    m = re.match(r"\d+", token)
    return int(m.group(0)) if m else None


def _split_files(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^(\[Файл \d+: .*?\])\n", text)
    if len(parts) < 3:
        return [("", text)]
    out = []
    for i in range(1, len(parts), 2):
        out.append((parts[i], parts[i + 1].strip()))
    return out


def _cut_units(body: str, unit: str, a: int, b: int | None) -> str:
    """Оставить в тексте только [Слайд a]…[Слайд b] (или страницы)."""
    pieces = re.split(rf"(?m)^(\[{unit} \d+\])\n?", body)
    if len(pieces) < 3:
        return body
    kept = []
    for i in range(1, len(pieces), 2):
        n = int(re.search(r"\d+", pieces[i]).group(0))
        if n >= a and (b is None or n <= b):
            kept.append(pieces[i] + "\n" + pieces[i + 1].strip())
    return "\n\n".join(kept)


def apply_range(text: str, instruction: str) -> tuple[str, str, str]:
    """Обрезаем текст по указанию «откуда и докуда».

    Возвращает (текст, что_сделал_код, остаток_указания_для_ИИ).
    Код понимает: файлы («только 2-й файл», «файлы 1 и 3»), слайды и страницы
    («со слайда 5 до 12», «страницы 3–7», «с 10 слайда до конца»), разделы
    («с раздела „Электролиз“ до конца», «до раздела „Итоги“»). Всё непонятое уходит ИИ.
    """
    instr = (instruction or "").strip()
    if not instr:
        return text, "", ""
    low = instr.lower().replace("ё", "е")
    notes: list[str] = []
    consumed: list[tuple[int, int]] = []
    files = _split_files(text)

    # 1) файлы
    m = re.search(r"(только\s+)?((?:(?:\d+|перв\w*|втор\w*|трет\w*|четв\w*|пят\w*)(?:-?[йгм]\w*)?[\s,и]*)+)\s*файл\w*", low) \
        or re.search(r"(только\s+)?файл\w*\s*((?:№?\s*\d+[\s,и]*)+)", low)
    if m and len(files) > 1:
        nums = {n for n in (_num(t) for t in re.findall(r"\d+|перв\w*|втор\w*|трет\w*|четв\w*|пят\w*", m.group(2))) if n}
        chosen = [f for i, f in enumerate(files, start=1) if i in nums]
        if chosen:
            files = chosen
            notes.append("взяты файлы " + ", ".join(str(n) for n in sorted(nums)))
            consumed.append(m.span())

    # 2) слайды / страницы
    for unit, words in (("Слайд", r"слайд\w*"), ("Страница", r"(?:страниц\w*|стр\.?)")):
        pats = [
            rf"(?:с|со)\s+{words}\s*№?\s*(\d+)\s*(?:до|по|-|–|—)\s*(?:{words}\s*)?(\d+|конца)",
            rf"(?:с|со)\s+(\d+)\s*(?:-?[гй]о)?\s*{words}\s*(?:до|по|-|–|—)\s*(\d+|конца)(?:\s*{words})?",
            rf"{words}\s*№?\s*(\d+)\s*(?:-|–|—|до|по)\s*(\d+)",
            rf"(?:с|со)\s+(\d+)\s*(?:до|по|-|–|—)\s*(\d+)\s*{words}",
            rf"(?:с|со)\s+{words}\s*№?\s*(\d+)()",
            rf"(?:с|со)\s+(\d+)\s*(?:-?[гй]о)?\s*{words}()",
            rf"до\s+{words}\s*№?\s*(\d+)",
        ]
        for i, pat in enumerate(pats):
            mm = re.search(pat, low)
            if not mm:
                continue
            if i == len(pats) - 1:
                a, b = 1, int(mm.group(1))
            else:
                a = int(mm.group(1))
                g2 = mm.group(2)
                b = None if (not g2 or g2 == "конца") else int(g2)
            if b is not None and b < a:
                a, b = b, a
            new_files = [(h, _cut_units(body, unit, a, b)) for h, body in files]
            if any(f"[{unit} " in body for _, body in files):
                files = [(h, body) for h, body in new_files if body.strip()]
                notes.append(f"{'слайды' if unit == 'Слайд' else 'страницы'} {a}–{b if b else 'конец'}")
            consumed.append(mm.span())
            break

    # 3) разделы по названию
    q = r"[«\"“„']([^»\"”“']+)[»\"”“']"
    body_all = "\n\n".join((h + "\n" + b).strip() for h, b in files)
    ms = re.search(rf"(?:с|со|от)\s+(?:раздела|главы|темы|параграфа|пункта|заголовка)\s*{q}", instr, re.I)
    me = re.search(rf"до\s+(?:раздела|главы|темы|параграфа|пункта|заголовка)\s*{q}", instr, re.I)
    if ms or me:
        lowbody = body_all.lower()
        start, end = 0, len(body_all)
        ok = True
        if ms:
            pos = lowbody.find(ms.group(1).lower().strip())
            if pos < 0:
                ok = False
            else:
                start = lowbody.rfind("\n", 0, pos) + 1
        if me and ok:
            pos = lowbody.find(me.group(1).lower().strip(), start + 1)
            if pos < 0:
                ok = False
            else:
                end = lowbody.rfind("\n", 0, pos) + 1 or pos
        if ok:
            body_all = body_all[start:end].strip()
            files = [("", body_all)]
            notes.append(join_parts(f"с раздела «{ms.group(1)}»" if ms else "",
                                    f"до раздела «{me.group(1)}»" if me else "до конца"))
            consumed += [m_.span() for m_ in (ms, me) if m_]
            m_end = re.search(r"до\s+конца", low)
            if m_end:
                consumed.append(m_end.span())

    result = "\n\n".join((h + "\n" + b).strip() for h, b in files).strip()
    if not result:
        raise MaterialError("После обрезки «откуда и докуда» текста не осталось. Проверь номера слайдов/страниц.")
    # Что осталось непонятым — отдаём ИИ.
    rest = list(low)
    for a, b in consumed:
        for i in range(a, min(b, len(rest))):
            rest[i] = " "
    rest_s = re.sub(r"\b(только|с|со|до|по|и|конца|читать|файл\w*|включительно)\b", " ", "".join(rest))
    leftover = instr if re.search(r"[а-яa-z]{3,}", rest_s) else ""
    return result, "; ".join(notes), leftover


# --- Объём и деление на куски ---------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Примерно: в русском тексте 1 токен ≈ 3,2 символа (берём с запасом)."""
    return int(len(text) / 3.2) + 1


_HEADING = re.compile(r"^(\[Файл \d+|\[Слайд \d+\]|\[Страница \d+\]|## |(глава|раздел|тема|часть|параграф|§)\s|\d+(\.\d+)*\.?\s+[А-ЯЁA-Z])", re.I)


def split_text(text: str, max_tokens: int) -> list[str]:
    """Делим большой текст на куски не больше max_tokens. Режем по возможности на заголовках,
    слайдах и страницах; слишком длинный абзац — по предложениям."""
    if estimate_tokens(text) <= max_tokens:
        return [text]
    max_chars = int(max_tokens * 3.2)
    blocks: list[str] = []
    for para in re.split(r"\n(?=\S)", text):
        if len(para) <= max_chars:
            blocks.append(para)
            continue
        cur = ""
        for sent in re.split(r"(?<=[.!?…])\s+", para):
            while len(sent) > max_chars:  # совсем без точек
                blocks.append(sent[:max_chars])
                sent = sent[max_chars:]
            if len(cur) + len(sent) + 1 > max_chars and cur:
                blocks.append(cur)
                cur = ""
            cur = f"{cur} {sent}".strip()
        if cur:
            blocks.append(cur)
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for b in blocks:
        is_head = bool(_HEADING.match(b))
        if cur and (cur_len + len(b) + 1 > max_chars or (is_head and cur_len > max_chars * 0.5)):
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(b)
        cur_len += len(b) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def allocate(total: int, sizes: list[int]) -> list[int]:
    """Распределить total вопросов по кускам пропорционально их размеру (метод наибольших остатков)."""
    if not sizes:
        return []
    s = sum(sizes) or 1
    raw = [total * x / s for x in sizes]
    base = [int(r) for r in raw]
    left = total - sum(base)
    order = sorted(range(len(sizes)), key=lambda i: raw[i] - base[i], reverse=True)
    for i in order[:left]:
        base[i] += 1
    return base


# --- Проверка цитат ------------------------------------------------------------------

def norm_for_match(s: str) -> str:
    s = fix_homoglyphs(unicodedata.normalize("NFKC", s or "")).lower().replace("ё", "е")
    s = re.sub(r"\[(файл \d+: [^\]]*|страница \d+|слайд \d+)\]", " ", s)
    s = re.sub(r"[^\w]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class QuoteIndex:
    """Быстрая проверка «эта цитата правда есть в тексте» (допуск: пробелы, регистр, знаки)."""

    def __init__(self, text: str):
        self.norm = norm_for_match(text)

    def contains(self, quote: str) -> bool:
        q = (quote or "").strip()
        if not q:
            return False
        # Цитата с пропуском «…» — каждая часть должна быть в тексте по порядку.
        parts = [norm_for_match(p) for p in re.split(r"\.\.\.|…|\[\.\.\.\]", q)]
        parts = [p for p in parts if p]
        if not parts or sum(len(p.split()) for p in parts) < 3:
            return False
        pos = 0
        for p in parts:
            i = self.norm.find(p, pos)
            if i < 0:
                return False
            pos = i + len(p)
        return True


def norm_subject(name: str) -> str:
    """«  Химия  », «химия», «ХИМИЯ» → «химия». Ё = Е."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", name or "")).strip().lower().replace("ё", "е")


def nice_subject(name: str) -> str:
    """Как показывать: лишние пробелы убраны, первая буква большая."""
    s = re.sub(r"\s+", " ", name or "").strip()
    return s[:1].upper() + s[1:] if s else s
