"""Generate a few synthetic GST invoices (+ ground-truth JSON) for smoke testing.

All companies, GSTINs and bank details are fictional and come from app/demo_data.py, so
they match the seeded vendor master. The full 100-case dataset generator
(scripts/generate_dataset.py) comes in M4.

Run from backend/:  uv run python ../scripts/make_sample_invoices.py
"""

import io
import json
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pypdfium2 as pdfium
from PIL import ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.demo_data import (  # noqa: E402
    BUYER_ADDRESS,
    BUYER_GSTIN,
    BUYER_NAME,
    KAVERI,
    NILGIRI,
    SHREE_GANESH,
    ZEPHYR,
    DemoVendor,
)

OUT = Path(__file__).resolve().parents[1] / "samples"
TWO = Decimal("0.01")


def money(x: Decimal) -> Decimal:
    return x.quantize(TWO, rounding=ROUND_HALF_UP)


def inr(x: Decimal) -> str:
    """Indian digit grouping: 1,23,456.00"""
    s = f"{x:.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{whole}.{frac}"


@dataclass
class Spec:
    name: str
    vendor: DemoVendor
    items: list[tuple[str, str, Decimal, Decimal, Decimal]]  # desc, hsn, qty, price, rate
    po: str | None
    expected_route: str
    scanned: bool = False
    gst_misprint: Decimal = Decimal(0)  # added to the printed CGST/IGST to break the maths


SPECS = [
    Spec(
        "sample-01-intrastate",
        KAVERI,
        [
            ("A4 copier paper, 75 gsm (ream)", "4802", Decimal(40), Decimal("245.00"), Decimal(12)),
            ("Gel pens, blue (box of 10)", "9608", Decimal(25), Decimal("120.00"), Decimal(18)),
            ("Box files, foolscap", "4820", Decimal(30), Decimal("85.50"), Decimal(18)),
        ],
        "PO-2026-0412",
        "auto_approve",
    ),
    Spec(
        "sample-02-interstate",
        NILGIRI,
        [
            ("Managed hosting - September 2026", "998315", Decimal(1), Decimal("38500.00"),
             Decimal(18)),
            ("Backup storage, 500 GB", "998315", Decimal(2), Decimal("2750.00"), Decimal(18)),
        ],
        None,
        "human_review",  # total above the ₹50,000 auto-approve limit
    ),
    Spec(
        "sample-03-scanned",
        SHREE_GANESH,
        [
            ("Corrugated boxes 18x12x10 in", "4819", Decimal(500), Decimal("32.40"), Decimal(12)),
            ("Packing tape 48 mm x 65 m", "3919", Decimal(60), Decimal("54.00"), Decimal(18)),
        ],
        "PO-2026-0398",
        "auto_approve",
        scanned=True,
    ),
    Spec(
        "sample-04-bad-tax",
        KAVERI,
        [
            ("Whiteboard markers (box of 12)", "9608", Decimal(10), Decimal("310.00"), Decimal(18)),
            ("Stapler, heavy duty", "8472", Decimal(4), Decimal("640.00"), Decimal(18)),
        ],
        "PO-2026-0431",
        "human_review",  # printed CGST is wrong, so line items + taxes != total
        gst_misprint=Decimal("150.00"),
    ),
    Spec(
        "sample-05-unknown-vendor",
        ZEPHYR,
        [
            ("Stage and lighting rental, 1 day", "997319", Decimal(1), Decimal("18000.00"),
             Decimal(18)),
        ],
        None,
        "human_review",  # vendor not in the vendor master
    ),
]


def build(spec: Spec, rng: random.Random) -> tuple[bytes, dict]:
    v = spec.vendor
    vendor_gstin, buyer_gstin = v.gstin, BUYER_GSTIN
    inv_no = f"{v.name.split()[0][:3].upper()}/26-27/{rng.randint(100, 999)}"
    inv_date = date(2026, 9, rng.randint(1, 28))
    due = inv_date + timedelta(days=30)
    acct, ifsc = v.bank_account, v.bank_ifsc
    intra = vendor_gstin[:2] == buyer_gstin[:2]

    lines, subtotal, tax = [], Decimal(0), Decimal(0)
    for desc, hsn, qty, price, rate in spec.items:
        amount = money(qty * price)
        subtotal += amount
        tax += amount * rate / 100
        lines.append(
            dict(description=desc, hsn_sac=hsn, quantity=qty, unit_price=price, tax_rate=rate,
                 amount=amount)
        )
    tax = money(tax)
    cgst = sgst = money(tax / 2) if intra else Decimal(0)
    igst = Decimal(0) if intra else tax
    total = subtotal + cgst + sgst + igst  # the correct total is printed...
    if intra:  # ...but a misprinted tax line breaks the arithmetic
        cgst += spec.gst_misprint
    else:
        igst += spec.gst_misprint

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 20 * mm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, y, v.name)
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y - 5 * mm, v.address)
    c.drawString(20 * mm, y - 10 * mm, f"GSTIN: {vendor_gstin}")
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(w - 20 * mm, y, "TAX INVOICE")
    c.setFont("Helvetica", 9)
    c.drawRightString(w - 20 * mm, y - 5 * mm, f"Invoice No: {inv_no}")
    c.drawRightString(w - 20 * mm, y - 10 * mm, f"Invoice Date: {inv_date:%d/%m/%Y}")
    c.drawRightString(w - 20 * mm, y - 15 * mm, f"Due Date: {due:%d/%m/%Y}")
    if spec.po:
        c.drawRightString(w - 20 * mm, y - 20 * mm, f"PO Ref: {spec.po}")

    y -= 32 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Bill To:")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y - 5 * mm, BUYER_NAME)
    c.drawString(20 * mm, y - 10 * mm, BUYER_ADDRESS)
    c.drawString(20 * mm, y - 15 * mm, f"GSTIN: {buyer_gstin}")

    y -= 28 * mm
    cols = [20, 28, 105, 125, 140, 160, 175]
    heads = ["#", "Description", "HSN/SAC", "Qty", "Rate", "GST %", "Amount"]
    c.setFillColor(colors.HexColor("#e8e8e8"))
    c.rect(18 * mm, y - 2 * mm, w - 36 * mm, 7 * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    for x, t in zip(cols, heads, strict=True):
        c.drawString(x * mm, y, t)
    c.setFont("Helvetica", 9)
    for i, li in enumerate(lines, 1):
        y -= 8 * mm
        c.drawString(cols[0] * mm, y, str(i))
        c.drawString(cols[1] * mm, y, li["description"])
        c.drawString(cols[2] * mm, y, li["hsn_sac"])
        c.drawString(cols[3] * mm, y, str(li["quantity"]))
        c.drawString(cols[4] * mm, y, inr(li["unit_price"]))
        c.drawString(cols[5] * mm, y, f"{li['tax_rate']}%")
        c.drawRightString(w - 20 * mm, y, inr(li["amount"]))

    y -= 14 * mm
    rows = [("Taxable Value", subtotal)]
    rows += [("CGST", cgst), ("SGST", sgst)] if intra else [("IGST", igst)]
    for label, val in rows:
        c.drawString(140 * mm, y, label)
        c.drawRightString(w - 20 * mm, y, inr(val))
        y -= 6 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(140 * mm, y - 2 * mm, "Grand Total (INR)")
    c.drawRightString(w - 20 * mm, y - 2 * mm, inr(total))

    y -= 22 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "Bank Details")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, y - 5 * mm, f"A/c No: XXXXXXXX{acct[-4:]}    IFSC: {ifsc}")
    c.drawString(20 * mm, y - 10 * mm, "This is a computer generated invoice.")
    c.showPage()
    c.save()

    truth = {
        "vendor_name": v.name,
        "vendor_gstin": vendor_gstin,
        "buyer_gstin": buyer_gstin,
        "invoice_number": inv_no,
        "invoice_date": inv_date.isoformat(),
        "due_date": due.isoformat(),
        "po_number": spec.po,
        "currency": "INR",
        "line_items": [{k: str(v) for k, v in li.items()} for li in lines],
        "subtotal": str(subtotal),
        "cgst": str(cgst),
        "sgst": str(sgst),
        "igst": str(igst),
        "total": str(total),
        "bank_account_last4": acct[-4:],
        "bank_ifsc": ifsc,
    }
    return buf.getvalue(), truth


def to_scan(pdf_bytes: bytes, rng: random.Random) -> bytes:
    """Rasterise, tilt and blur so it looks like a phone scan."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    img = pdf[0].render(scale=1.6).to_pil().convert("L")
    pdf.close()
    img = img.rotate(rng.uniform(-2.5, 2.5), expand=True, fillcolor=235)
    img = img.filter(ImageFilter.GaussianBlur(0.8))
    img = img.point(lambda p: int(40 + p * 0.78))  # lower contrast
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rng = random.Random(20261007)
    for spec in SPECS:
        pdf, truth = build(spec, rng)
        if spec.scanned:
            (OUT / f"{spec.name}.png").write_bytes(to_scan(pdf, rng))
        else:
            (OUT / f"{spec.name}.pdf").write_bytes(pdf)
        case = {"expected_extraction": truth, "expected_route": spec.expected_route}
        (OUT / f"{spec.name}.expected.json").write_text(json.dumps(case, indent=2))
        print("wrote", spec.name)


if __name__ == "__main__":
    main()
