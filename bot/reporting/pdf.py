"""PDF-отчёт (reportlab). Шрифт DejaVu из комплекта matplotlib — с кириллицей."""
import io
import logging
import os
from datetime import datetime, timezone

import matplotlib
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas

logger = logging.getLogger(__name__)

_FONT = "DejaVu"
_font_registered = False


def _ensure_font() -> None:
    global _font_registered
    if _font_registered:
        return
    try:
        font_path = os.path.join(os.path.dirname(matplotlib.__file__),
                                 "mpl-data", "fonts", "ttf", "DejaVuSans.ttf")
        pdfmetrics.registerFont(TTFont(_FONT, font_path))
        _font_registered = True
    except Exception as ex:
        logger.warning("шрифт DejaVu не зарегистрирован: %s", ex)


SENT_NAMES = {"positive": "позитив", "neutral": "нейтрально", "negative": "негатив"}


def generate_pdf(items: list[dict], stats: dict, title: str = "Отчёт мониторинга") -> bytes | None:
    """PDF-отчёт со сводкой и таблицей постов. Байты или None при ошибке."""
    _ensure_font()
    try:
        buf = io.BytesIO()
        c = pdf_canvas.Canvas(buf, pagesize=A4)
        width, height = A4
        margin = 15 * mm
        y = height - margin

        def font(size: int, bold: bool = False):
            c.setFont(_FONT, size)

        # Заголовок
        font(16)
        c.drawString(margin, y, title)
        y -= 8 * mm
        font(9)
        c.drawString(margin, y, datetime.now(timezone.utc).strftime(
            "Сформирован %d.%m.%Y %H:%M UTC"))
        y -= 10 * mm

        # Сводка
        sent = stats.get("sentiment", {})
        font(11)
        c.drawString(margin, y, f"Всего найдено: {stats.get('total', len(items))}")
        y -= 6 * mm
        font(9)
        c.drawString(margin, y,
                     f"Тональность: {SENT_NAMES.get('positive')} {sent.get('positive', 0)} · "
                     f"{SENT_NAMES.get('neutral')} {sent.get('neutral', 0)} · "
                     f"{SENT_NAMES.get('negative')} {sent.get('negative', 0)}")
        y -= 6 * mm
        by_platform = stats.get("by_platform", {})
        c.drawString(margin, y, "Платформы: " +
                     ", ".join(f"{k} — {v}" for k, v in list(by_platform.items())[:8]))
        y -= 10 * mm

        # Посты
        for i, it in enumerate(items[:40], 1):
            if y < margin + 30 * mm:
                c.showPage()
                y = height - margin
                font(9)
            font(10)
            head = f"{i}. [{it.get('platform', '?')}] {it.get('author', '')[:30]}"
            c.drawString(margin, y, head)
            y -= 5 * mm
            font(9)
            text = (it.get("text") or "").replace("\n", " ")[:180]
            for line in _wrap(c, text, width - 2 * margin):
                c.drawString(margin, y, line)
                y -= 4.5 * mm
            c.setFillColorRGB(0.3, 0.3, 0.5)
            url = (it.get("url") or "")[:90]
            c.drawString(margin, y, url)
            c.setFillColorRGB(0, 0, 0)
            y -= 8 * mm

        c.save()
        return buf.getvalue()
    except Exception as ex:
        logger.warning("PDF не построен: %s", ex)
        return None


def _wrap(c, text: str, max_width: float) -> list[str]:
    words = text.split()
    lines, current = [], ""
    for w in words:
        test = (current + " " + w).strip()
        if c.stringWidth(test, _FONT, 9) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines[:4]
