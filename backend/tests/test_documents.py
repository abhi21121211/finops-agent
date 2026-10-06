import io

from PIL import Image

from app.agents.tools.documents import render, sniff_content_type
from tests.conftest import make_pdf


def test_sniff_by_magic_bytes():
    assert sniff_content_type(make_pdf()) == "application/pdf"
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")
    assert sniff_content_type(buf.getvalue()) == "image/png"
    assert sniff_content_type(b"<html>not an invoice</html>") is None


def test_pdf_renders_pages_and_text():
    doc = render(make_pdf("Invoice No: ABC-123"), "application/pdf", max_pages=5)
    assert len(doc.pages) == 1
    assert doc.pages[0].startswith(b"\xff\xd8")  # JPEG
    assert "ABC-123" in doc.text


def test_large_image_downscaled_to_jpeg():
    buf = io.BytesIO()
    Image.new("RGBA", (5000, 3000), "white").save(buf, format="PNG")
    doc = render(buf.getvalue(), "image/png", max_pages=5)
    img = Image.open(io.BytesIO(doc.pages[0]))
    assert img.format == "JPEG"
    assert max(img.size) == 2000
    assert doc.text == ""
