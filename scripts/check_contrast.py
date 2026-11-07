"""Проверка контраста палитр (WCAG): текст на фоне, приглушённый текст, кнопки, подсветка ответа.
Запуск: python scripts/check_contrast.py  — печатает таблицу и ⚠ там, где контраст мал."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "static" / "app.css").read_text()


def lum(hexc):
    h = hexc.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    rgb = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def ratio(a, b):
    la, lb = sorted([lum(a), lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


base = {}
pals = {}
for m in re.finditer(r'(?:\[data-palette="(\w+)"\]|:root, \[data-palette="classic"\])\s*\{([^}]*)\}', CSS):
    name = m.group(1) or "classic"
    vars_ = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})", m.group(2)))
    pals[name] = {**pals.get("classic", {}), **vars_}

CHECKS = [  # (что, цвет, фон, минимум)
    ("текст/фон", "text", "bg", 7.0), ("текст/карточка", "text", "surface", 7.0),
    ("приглуш./фон", "muted", "bg", 4.5), ("приглуш./плашка", "muted", "surface-2", 4.0),
    ("кнопка", "accent-text", "accent", 4.5), ("текст на зелёном", "text", "ok-bg", 7.0),
    ("текст на красном", "text", "bad-bg", 7.0), ("знак ✓ (рамка)", "ok", "ok-bg", 3.0),
    ("знак ✕ (рамка)", "bad", "bad-bg", 3.0), ("рамка ✓ / фон", "ok", "bg", 3.0), ("рамка ✕ / фон", "bad", "bg", 3.0),
]
bad = 0
for name, v in pals.items():
    line = []
    for label, a, b, need in CHECKS:
        r = ratio(v[a], v[b])
        flag = "" if r >= need else " ⚠"
        bad += bool(flag)
        line.append(f"{label} {r:.1f}{flag}")
    print(f"{name:9s} | " + " | ".join(line))
print("Всё в норме" if not bad else f"Мало контраста: {bad}")
