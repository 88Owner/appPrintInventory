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


def _normalize_product_name(name: str) -> str:
    """Bỏ phần sau dấu '+' (thường là ghi chú/biến thể phụ), gọn khoảng trắng."""
    s = (name or "").strip()
    if "+" in s:
        s = s.split("+", 1)[0].strip()
    return " ".join(s.split())


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
    """Xuống dòng theo từ — không cắt bằng ellipsis; từ quá dài thì tách theo ký tự."""
    text = " ".join((text or "").split())
    if not text:
        return []

    def _flush(cur: list[str]) -> None:
        if cur:
            lines.append(" ".join(cur))

    def _emit_char_chunks(word: str) -> list[str]:
        chunks: list[str] = []
        chunk = ""
        for ch in word:
            trial = chunk + ch
            if c.stringWidth(trial, font, size) <= max_width_pt:
                chunk = trial
            else:
                if chunk:
                    chunks.append(chunk)
                chunk = ch
        if chunk:
            chunks.append(chunk)
        return chunks

    words = text.split(" ")
    lines: list[str] = []
    cur: list[str] = []

    for w in words:
        if c.stringWidth(w, font, size) > max_width_pt:
            _flush(cur)
            cur = []
            for piece in _emit_char_chunks(w):
                lines.append(piece)
            continue

        trial = (" ".join(cur + [w])).strip()
        if not cur:
            cur = [w]
            continue
        if c.stringWidth(trial, font, size) <= max_width_pt:
            cur.append(w)
        else:
            _flush(cur)
            cur = [w]

    _flush(cur)
    return lines


def _looks_like_variant_token(word: str) -> bool:
    w = (word or "").strip().lower()
    if not w:
        return False
    # common patterns like "2mx2m25", "2m", "10x20", etc.
    if "mx" in w or "x" in w:
        has_digit = any(ch.isdigit() for ch in w)
        return has_digit
    return False


def _looks_like_count_phrase(w1: str, w2: str) -> bool:
    a = (w1 or "").strip()
    b = (w2 or "").strip()
    if not a or not b:
        return False
    if not a.isdigit():
        return False
    # second token likely Vietnamese word like "lớp", "cái", "cuộn"...
    return any(ch.isalpha() for ch in b)


def _split_name_lines(c: canvas.Canvas, name: str, max_width_pt: float, font: str, size: int) -> list[str]:
    """Tách tên thành các dòng đầy đủ (không dùng …)."""
    clean = _normalize_product_name(name)
    if not clean:
        return []

    words = clean.split(" ")

    # Rule A: "1 lớp 1mx0,7m Caro Nâu" / "Xương Rồng Xanh Lá" sau size
    if len(words) >= 4 and _looks_like_count_phrase(words[0], words[1]) and _looks_like_variant_token(words[2]):
        l1 = " ".join(words[:2])
        l2 = words[2]
        tail = " ".join(words[3:])
        out = [l1, l2]
        out.extend(_wrap_words(c, tail, max_width_pt, font, size))
        return [ln for ln in out if ln]

    # Rule B: "Rido 2mx2m25 Vương Miệng Xanh Lá" => brand, size, phần tên còn lại (có thể nhiều dòng)
    if len(words) >= 3 and _looks_like_variant_token(words[1]):
        l1 = words[0]
        l2 = words[1]
        tail = " ".join(words[2:])
        out = [l1, l2]
        out.extend(_wrap_words(c, tail, max_width_pt, font, size))
        return [ln for ln in out if ln]

    return [ln for ln in _wrap_words(c, clean, max_width_pt, font, size) if ln]


def generate_labels_pdf(
    rows: Iterable[LabelRow],
    output_path: str,
    *,
    page_w_mm: float = 72.0,
    page_h_mm: float = 22.0,
    two_up: bool = True,
) -> None:
    """
    1 page = 1 khổ 72x22mm.
    two_up=True: một khổ in 2 tem (trái/phải), mỗi tem một QR theo SKU.
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

    def draw_one(label: LabelRow, *, x0: float, y0: float) -> None:
        # Layout: QR left, text right (tối đa 3 dòng name + 1 dòng sku)
        inner_h = page_h - 2 * pad_y
        # QR nhỏ hơn một chút để vùng chữ rộng hơn (tên dài dễ fit)
        qr_size = min(15.0 * mm, inner_h)
        inner_y = y0

        qr_x = x0
        qr_y = inner_y + (inner_h - qr_size) / 2

        text_gap = 1.2 * mm
        text_x = qr_x + qr_size + text_gap
        text_w = max(1.0 * mm, col_w - qr_size - text_gap)

        # QR image (left)
        img_bytes = _qr_png_bytes(label.sku)
        img = Image.open(BytesIO(img_bytes))
        img_w, img_h = img.size
        img_reader = ImageReader(img)

        scale = min(qr_size / img_w, qr_size / img_h)
        draw_w = img_w * scale
        draw_h = img_h * scale
        c.drawImage(
            img_reader,
            qr_x + (qr_size - draw_w) / 2,
            qr_y + (qr_size - draw_h) / 2,
            width=draw_w,
            height=draw_h,
            preserveAspectRatio=True,
            mask="auto",
        )

        display_name = _normalize_product_name(label.name)
        base_size = 7
        min_size = 3
        line_gap = 0.4 * mm

        def _sku_lines(sz: int) -> list[str]:
            wrapped = _wrap_words(c, label.sku, text_w, sku_font, sz)
            if wrapped:
                return wrapped
            return [_fit_text(c, label.sku, text_w, sku_font, sz)]

        drew = False
        for size in range(base_size, min_size - 1, -1):
            name_lines = _split_name_lines(c, display_name, text_w, name_font, size)
            if not name_lines:
                name_lines = [""]
            sku_lines = _sku_lines(size)
            all_lines = name_lines + sku_lines

            total_h = len(all_lines) * size + (len(all_lines) - 1) * line_gap
            if total_h <= inner_h + 0.1:
                y_top = inner_y + inner_h - (inner_h - total_h) / 2
                y = y_top - size

                c.setFont(name_font, size)
                for ln in name_lines:
                    c.drawString(text_x, y, ln)
                    y -= (size + line_gap)

                c.setFont(sku_font, size)
                for ln in sku_lines:
                    c.drawString(text_x, y, ln)
                    y -= (size + line_gap)
                drew = True
                break

        if not drew:
            size = min_size
            name_lines = _split_name_lines(c, display_name, text_w, name_font, size) or [""]
            sku_lines = _sku_lines(size)
            all_lines = name_lines + sku_lines
            total_h = len(all_lines) * size + (len(all_lines) - 1) * line_gap
            y_top = inner_y + inner_h - (inner_h - total_h) / 2
            y = y_top - size
            c.setFont(name_font, size)
            for ln in name_lines:
                c.drawString(text_x, y, ln)
                y -= (size + line_gap)
            c.setFont(sku_font, size)
            for ln in sku_lines:
                c.drawString(text_x, y, ln)
                y -= (size + line_gap)

    rows_list = list(rows)
    i = 0
    while i < len(rows_list):
        c.setFillColorRGB(0, 0, 0)

        if two_up:
            left = rows_list[i]
            right = rows_list[i + 1] if (i + 1) < len(rows_list) else None

            x_left = pad_x
            y0 = pad_y
            draw_one(left, x0=x_left, y0=y0)

            if right is not None:
                x_right = pad_x + (col_w + gap)
                draw_one(right, x0=x_right, y0=y0)

            i += 2
        else:
            x0 = pad_x
            y0 = pad_y
            draw_one(rows_list[i], x0=x0, y0=y0)
            i += 1

        c.showPage()

    c.save()
