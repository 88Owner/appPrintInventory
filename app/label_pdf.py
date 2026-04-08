from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Iterable

import qrcode
from PIL import Image
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader


@dataclass(frozen=True)
class LabelRow:
    name: str
    sku: str


_FONT_REGISTERED = False


def _ensure_vietnamese_font() -> tuple[str, str]:
    """
    ReportLab built-in fonts don't cover Vietnamese well.
    On Windows, Arial supports Vietnamese and is usually present.
    """
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return "AppFont", "AppFont"

    # Prefer Arial; fall back to Segoe UI if available.
    candidates = [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\ARIAL.TTF"),
        Path(r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\SEGOEUI.TTF"),
    ]
    font_path = next((p for p in candidates if p.exists()), None)
    if font_path:
        pdfmetrics.registerFont(TTFont("AppFont", str(font_path)))
        _FONT_REGISTERED = True
        return "AppFont", "AppFont"

    # Fallback (may render without accents correctly on some systems, but not guaranteed)
    _FONT_REGISTERED = True
    return "Helvetica", "Helvetica"


def _qr_png_bytes(value: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=1,
    )
    qr.add_data(value)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _fit_text(c: canvas.Canvas, text: str, max_width_pt: float, font: str, size: int) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    if c.stringWidth(text, font, size) <= max_width_pt:
        return text
    # simple truncate with ellipsis
    ell = "…"
    lo, hi = 0, len(text)
    best = ell
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid].rstrip() + ell
        if c.stringWidth(cand, font, size) <= max_width_pt:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _wrap_words(c: canvas.Canvas, text: str, max_width_pt: float, font: str, size: int) -> list[str]:
    text = " ".join((text or "").split())
    if not text:
        return []

    words = text.split(" ")
    lines: list[str] = []
    cur: list[str] = []

    for w in words:
        trial = (" ".join(cur + [w])).strip()
        if not cur:
            cur = [w]
            continue
        if c.stringWidth(trial, font, size) <= max_width_pt:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]

    if cur:
        lines.append(" ".join(cur))
    return lines


def _draw_centered_name(
    c: canvas.Canvas,
    *,
    text: str,
    x_left: float,
    y_top: float,
    width: float,
    font: str,
    max_lines: int = 2,
    max_size: int = 8,
    min_size: int = 5,
    line_gap: float = 0.6 * mm,
) -> float:
    """
    Draw product name centered. Try 1-2 lines and shrink font to fit width.
    Returns total height used (pt).
    """
    clean = " ".join((text or "").split())
    if not clean:
        return 0.0

    for size in range(max_size, min_size - 1, -1):
        c.setFont(font, size)
        lines = _wrap_words(c, clean, width, font, size)
        if not lines:
            continue
        lines = lines[:max_lines]
        # If we had to cut to max_lines, try to at least fit last line with ellipsis
        if len(lines) == max_lines and " ".join(lines) != clean:
            lines[-1] = _fit_text(c, lines[-1], width, font, size)

        if all(c.stringWidth(ln, font, size) <= width for ln in lines):
            total_h = len(lines) * size + (len(lines) - 1) * line_gap
            # draw from top down
            y = y_top - size
            for ln in lines:
                c.drawCentredString(x_left + width / 2, y, ln)
                y -= (size + line_gap)
            return total_h

    # Fallback: single line truncated
    size = min_size
    c.setFont(font, size)
    c.drawCentredString(x_left + width / 2, y_top - size, _fit_text(c, clean, width, font, size))
    return size


def generate_labels_pdf(
    rows: Iterable[LabelRow],
    output_path: str,
    *,
    page_w_mm: float = 72.0,
    page_h_mm: float = 22.0,
    two_up: bool = True,
) -> None:
    """
    1 page = 1 tem 72x22mm.
    two_up=True: chia đôi chiều ngang, in 2 tem giống nhau trên cùng 1 tem (trái/phải).
    """
    page_w = page_w_mm * mm
    page_h = page_h_mm * mm
    c = canvas.Canvas(output_path, pagesize=(page_w, page_h))

    # Layout constants
    pad_x = 1.2 * mm
    pad_y = 1.0 * mm
    gap = 0.8 * mm

    col_count = 2 if two_up else 1
    col_w = (page_w - pad_x * 2 - (gap * (col_count - 1))) / col_count

    name_font, sku_font = _ensure_vietnamese_font()

    for row in rows:
        c.setFillColorRGB(0, 0, 0)

        for col in range(col_count):
            x0 = pad_x + col * (col_w + gap)
            y0 = pad_y

            # Name (top) - centered, wrap/shrink to show full as much as possible
            name_top = page_h - y0
            name_used_h = _draw_centered_name(
                c,
                text=row.name,
                x_left=x0,
                y_top=name_top,
                width=col_w,
                font=name_font,
                max_lines=2,
                max_size=8,
                min_size=5,
            )

            # QR image (middle)
            img_bytes = _qr_png_bytes(row.sku)
            img = Image.open(BytesIO(img_bytes))
            img_w, img_h = img.size
            img_reader = ImageReader(img)

            # Allocate square-ish area for QR
            qr_h = 13.0 * mm
            qr_y = page_h - y0 - name_used_h - (1.2 * mm) - qr_h
            qr_w = col_w

            # Keep aspect ratio, fit within qr_w x qr_h
            scale = min(qr_w / img_w, qr_h / img_h)
            draw_w = img_w * scale
            draw_h = img_h * scale
            c.drawImage(
                img_reader,
                x0 + (qr_w - draw_w) / 2,
                qr_y + (qr_h - draw_h) / 2,
                width=draw_w,
                height=draw_h,
                preserveAspectRatio=True,
                mask="auto",
            )

            # SKU text (bottom)
            sku_size = 7
            c.setFont(sku_font, sku_size)
            sku_y = y0 + 0.5 * mm
            sku_text = _fit_text(c, row.sku, col_w, sku_font, sku_size)
            c.drawCentredString(x0 + col_w / 2, sku_y, sku_text)

        c.showPage()

    c.save()
