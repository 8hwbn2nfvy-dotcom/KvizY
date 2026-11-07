"""Тестовые файлы: PDF, Word, PowerPoint, txt — создаются на лету."""
from __future__ import annotations

import io
from pathlib import Path

CHEM = [
    ("Электролиз", [
        "Электролиз — это процесс разложения вещества электрическим током в растворе или расплаве.",
        "При электролизе на катоде идёт восстановление, а на аноде идёт окисление ионов.",
        "Катод подключают к отрицательному полюсу источника тока, а анод к положительному.",
        "Электролиз расплава хлорида натрия даёт металлический натрий и газообразный хлор.",
    ]),
    ("Кислоты и основания", [
        "Кислоты в водном растворе отдают ионы водорода и меняют цвет лакмуса на красный.",
        "Основания в растворе дают гидроксид-ионы и окрашивают фенолфталеин в малиновый цвет.",
        "Реакция кислоты с основанием называется нейтрализацией и даёт соль и воду.",
        "Сильные кислоты почти полностью распадаются на ионы даже в разбавленном растворе.",
    ]),
]


def txt_bytes() -> bytes:
    out = []
    for h, sents in CHEM:
        out.append(f"## {h}")
        out += [f"- {s}" for s in sents]
    out.append("1945 год стал годом окончания войны, это просто число в начале строки для проверки чистки.")
    return "\n".join(out).encode()


def docx_bytes() -> bytes:
    from docx import Document
    d = Document()
    for h, sents in CHEM:
        d.add_heading(h, level=1)
        for s in sents:
            d.add_paragraph(s)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def pptx_bytes() -> bytes:
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    for h, sents in CHEM:
        for s in sents:
            sl = prs.slides.add_slide(prs.slide_layouts[5])
            sl.shapes.title.text = h
            tb = sl.shapes.add_textbox(Inches(1), Inches(2), Inches(8), Inches(2))
            tb.text_frame.text = s
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def pdf_bytes() -> bytes | None:
    """PDF с русским текстом. В одном слове специально латинская «ĸ» вместо «к» — проверяем чистку."""
    if not FONT.exists():
        return None
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    pdfmetrics.registerFont(TTFont("DejaVu", str(FONT)))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for h, sents in CHEM:
        c.setFont("DejaVu", 12)
        y = 800
        c.drawString(50, y, h)
        for s in sents:
            y -= 24
            c.drawString(50, y, s.replace("катоде", "ĸатоде"))
        c.showPage()
    c.save()
    return buf.getvalue()


HTML = ("<html><head><title>Окислительно-восстановительные реакции</title><script>var x=1;</script></head><body>"
        "<nav>Меню сайта Главная Контакты</nav><article><h1>Окислительно-восстановительные реакции</h1>"
        "<p>В окислительно-восстановительной реакции один атом отдаёт электроны, а другой их принимает.</p>"
        "<p>Окислитель принимает электроны и при этом сам восстанавливается в ходе реакции.</p>"
        "<p>Восстановитель отдаёт электроны и при этом сам окисляется, повышая степень окисления.</p>"
        "<p>Степень окисления показывает условный заряд атома в соединении при ионной модели связи.</p>"
        "<p>Коррозия железа во влажном воздухе тоже относится к окислительно-восстановительным процессам.</p>"
        "<p>Горение метана в кислороде даёт углекислый газ и воду, а метан выступает восстановителем.</p>"
        "<p>Метод электронного баланса помогает расставить коэффициенты в уравнениях таких реакций.</p>"
        "<p>Перманганат калия в кислой среде является очень сильным окислителем для многих веществ.</p>"
        "</article><footer>Все права защищены</footer></body></html>")
