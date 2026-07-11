"""Render Telegram bot replies as PNG status cards."""

import os
import textwrap
from io import BytesIO
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


def _font(size: int, bold: bool = False):
    """Load a Chinese-capable system font while retaining a portable fallback."""

    candidates = [
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "msyhbd.ttc" if bold else "msyh.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def render_telegram_card(title: str, lines: Iterable[str], badge: str = "Telegram Media Downloader") -> BytesIO:
    """Create a dark PNG card containing a title and wrapped status rows."""

    width = 1080
    padding = 64
    title_font = _font(42, bold=True)
    body_font = _font(29)
    badge_font = _font(23, bold=True)
    wrapped = []
    for line in lines:
        wrapped.extend(textwrap.wrap(str(line), width=42, replace_whitespace=False) or [""])
    line_height = 48
    height = max(420, 210 + len(wrapped) * line_height + padding)
    image = Image.new("RGB", (width, height), "#0b1220")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, width - 20, height - 20), radius=36, fill="#111c2e", outline="#26364f", width=3)
    draw.rounded_rectangle((padding, 54, padding + 390, 100), radius=23, fill="#173967")
    draw.text((padding + 22, 62), badge, font=badge_font, fill="#7dc4ff")
    draw.text((padding, 132), title, font=title_font, fill="#f4f7fb")
    draw.line((padding, 195, width - padding, 195), fill="#2a3a52", width=2)
    y = 228
    for line in wrapped:
        draw.text((padding, y), line, font=body_font, fill="#d4deeb")
        y += line_height
    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    output.seek(0)
    output.name = "telegram-card.png"
    return output
