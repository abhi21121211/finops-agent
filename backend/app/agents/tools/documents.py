"""Turn uploaded files into page images (for the vision model) and text (when the PDF has a
text layer). Pure code, no LLM."""

import io
import threading
from dataclasses import dataclass

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageOps

MAX_SIDE_PX = 2000
JPEG_QUALITY = 85

SUPPORTED_TYPES = {"application/pdf", "image/png", "image/jpeg"}

# pdfium is not thread-safe; concurrent renders from worker threads can deadlock.
_PDFIUM_LOCK = threading.Lock()


@dataclass
class RenderedDocument:
    pages: list[bytes]  # JPEG bytes, one per page
    text: str  # empty for scans and images
    page_count: int  # pages in the source (may exceed len(pages))


def _to_jpeg(img: Image.Image) -> bytes:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def sniff_content_type(data: bytes) -> str | None:
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return None


def render(data: bytes, content_type: str, max_pages: int) -> RenderedDocument:
    if content_type == "application/pdf":
        return _render_pdf(data, max_pages)
    with Image.open(io.BytesIO(data)) as img:
        return RenderedDocument(pages=[_to_jpeg(img)], text="", page_count=1)


def _render_pdf(data: bytes, max_pages: int) -> RenderedDocument:
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data)
        try:
            page_count = len(pdf)
            images = [
                # scale 2 ≈ 144 dpi: readable for small print without huge payloads
                pdf[i].render(scale=2).to_pil()
                for i in range(min(page_count, max_pages))
            ]
        finally:
            pdf.close()
    pages = [_to_jpeg(img) for img in images]

    texts = []
    with pdfplumber.open(io.BytesIO(data)) as doc:
        for page in doc.pages[:max_pages]:
            texts.append(page.extract_text() or "")
    text = "\n\n".join(f"--- page {i + 1} ---\n{t}" for i, t in enumerate(texts) if t.strip())
    return RenderedDocument(pages=pages, text=text, page_count=page_count)
