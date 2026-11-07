"""Рисует иконки приложения и экраны запуска iOS (static/icons). Запуск: python scripts/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "static" / "icons"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def icon(size: int, pad: float = 0.0) -> Image.Image:
    """Чёрный квадрат, белые буквы «KY» и галочка."""
    img = Image.new("RGB", (size, size), "#111111")
    d = ImageDraw.Draw(img)
    inner = size * (1 - 2 * pad)
    f = ImageFont.truetype(FONT, int(inner * 0.42))
    text = "KY"
    box = d.textbbox((0, 0), text, font=f)
    w, h = box[2] - box[0], box[3] - box[1]
    x, y = (size - w) / 2 - box[0], (size - h) / 2 - box[1] - inner * 0.06
    d.text((x, y), text, font=f, fill="#ffffff")
    # галочка под буквами
    cx, cy, s = size / 2, size / 2 + inner * 0.25, inner * 0.09
    d.line([(cx - s * 1.4, cy), (cx - s * 0.3, cy + s), (cx + s * 1.6, cy - s * 0.9)], fill="#ffffff",
           width=max(2, int(inner * 0.035)), joint="curve")
    return img


def splash(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), "#ffffff")
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(FONT, int(min(w, h) * 0.11))
    box = d.textbbox((0, 0), "KvizY", font=f)
    d.text(((w - (box[2] - box[0])) / 2 - box[0], (h - (box[3] - box[1])) / 2 - box[1]), "KvizY", font=f, fill="#111111")
    return img


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    icon(180).save(OUT / "apple-touch-icon.png", optimize=True)
    icon(192).save(OUT / "icon-192.png", optimize=True)
    icon(512).save(OUT / "icon-512.png", optimize=True)
    icon(512, pad=0.1).save(OUT / "icon-maskable-512.png", optimize=True)
    for w, h in [(1290, 2796), (1179, 2556), (1170, 2532), (750, 1334), (1640, 2360), (1668, 2388), (2048, 2732)]:
        splash(w, h).save(OUT / f"splash-{w}x{h}.png", optimize=True)
    print("ok")
