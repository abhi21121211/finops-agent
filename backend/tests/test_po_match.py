from decimal import Decimal as D

from app.reconcile.po_match import POData, POLineData, match_po, similarity
from app.schemas.extraction import ExtractedInvoice
from tests.conftest import sample_extraction

KAV = "27KAVCO4821K1ZJ"


def po(*lines, number="PO-1", vendor=KAV, status="open", total="1000") -> POData:
    return POData(
        "po-id", number, vendor, "Kaveri Office Supplies LLP", D(total), status, list(lines)
    )


def line(desc="Widget", qty="2", price="500", billed="0") -> POLineData:
    return POLineData(desc, D(qty), D(price), D(billed))


def inv(**overrides) -> ExtractedInvoice:
    return ExtractedInvoice.model_validate(sample_extraction(**overrides)["invoice"])


def li(desc, qty, price, rate=18):
    return {
        "description": desc,
        "quantity": qty,
        "unit_price": price,
        "tax_rate": rate,
        "amount": qty * price,
    }


async def test_exact_match():
    r = await match_po(inv(), [po(line())])
    assert r.status == "matched" and r.found_by == "po_number"
    assert r.lines[0].status == "ok"


async def test_po_number_formatting_ignored():
    r = await match_po(inv(po_number="po 1"), [po(line(), number="PO-1")])
    assert r.status == "matched"


async def test_cited_po_missing():
    r = await match_po(inv(po_number="PO-404"), [po(line())])
    assert r.status == "no_po" and "PO-404" in r.explanation


async def test_no_po_number_falls_back_to_vendor_and_amount():
    r = await match_po(inv(po_number=None), [po(line(), total="1000.00")])
    assert r.status == "matched" and r.found_by == "vendor_amount"


async def test_no_po_number_and_no_amount_match():
    r = await match_po(inv(po_number=None), [po(line(), total="5000")])
    assert r.status == "no_po"


async def test_price_within_two_percent_ok_above_is_mismatch():
    ok = await match_po(
        inv(
            line_items=[li("Widget", 2, 509)], subtotal=1018, total=1201.24, cgst=91.62, sgst=91.62
        ),
        [po(line())],
    )
    assert ok.status == "matched"
    bad = await match_po(
        inv(line_items=[li("Widget", 2, 520)], subtotal=1040, total=1227.2, cgst=93.6, sgst=93.6),
        [po(line())],
    )
    assert bad.status == "mismatch" and "+4.0%" in bad.explanation


async def test_partial_delivery_is_matched():
    r = await match_po(inv(), [po(line(qty="10"))])
    assert r.status == "matched" and r.lines[0].quantity_available == D("10")


async def test_over_billing_across_invoices():
    r = await match_po(inv(), [po(line(qty="10", billed="9"))])
    assert r.status == "mismatch"
    assert "only 1 of 10 remain" in r.explanation and "9 already billed" in r.explanation


async def test_line_not_on_po():
    lines = [li("Widget", 2, 500), li("Express delivery charge", 1, 300)]
    r = await match_po(
        inv(line_items=lines, subtotal=1300, total=1534, cgst=117, sgst=117), [po(line())]
    )
    assert r.status == "mismatch" and "Express delivery" in r.explanation


async def test_po_for_other_vendor():
    r = await match_po(inv(), [po(line(), vendor="29NILCC7310N1ZD")])
    assert r.status == "mismatch" and "not to this invoice's vendor" in r.explanation


async def test_closed_po():
    r = await match_po(inv(), [po(line(), status="closed")])
    assert r.status == "mismatch" and "closed" in r.explanation


async def test_lines_assigned_one_to_one_by_best_similarity():
    p = po(
        line("Gel pens, blue (box of 10)", "25", "120"),
        line("Gel pens, black (box of 10)", "25", "120"),
    )
    lines = [li("Gel pens black, box of 10", 25, 120), li("Gel pens blue, box of 10", 25, 120)]
    r = await match_po(inv(line_items=lines, subtotal=6000, cgst=540, sgst=540, total=7080), [p])
    assert [m.po_line for m in r.lines] == [1, 0]


async def test_uncertain_description_asks_judge():
    asked = []

    async def judge(pairs):
        asked.extend(pairs)
        return [True] * len(pairs)

    p = po(line("Toner cartridge HP 88A", "2", "500"))
    lines = [li("CC388A black laser toner", 2, 500)]
    r = await match_po(inv(line_items=lines), [p], judge=judge)
    assert asked and r.status == "matched" and r.lines[0].matched_by == "llm"


async def test_judge_saying_no_leaves_line_unmatched():
    async def judge(pairs):
        return [False] * len(pairs)

    p = po(line("Toner cartridge HP 88A", "2", "500"))
    r = await match_po(inv(line_items=[li("CC388A black laser toner", 2, 500)]), [p], judge=judge)
    assert r.status == "mismatch"


def test_similarity_bounds():
    assert similarity("A4 copier paper, 75 gsm (ream)", "A4 Copier Paper 75gsm ream") > 0.75
    assert similarity("Stapler, heavy duty", "Corrugated boxes") < 0.35


async def test_truncated_lot_descriptions_paired_by_price():
    """Regression: the PDF truncates long descriptions, so lots look identical; pairing on
    text alone swapped two lines and reported false price mismatches."""
    p = po(
        line("Security guard, 12h shift (per day) (lot 1)", "5", "950.00"),
        line("Security guard, 12h shift (per day) (lot 2)", "4", "997.50"),
    )
    lines = [
        li("Security guard, 12h shift (per day) (lot …", 4, D("997.50")),
        li("Security guard, 12h shift (per day) (lot …", 5, D("950.00")),
    ]
    sub = D("8740.00")
    r = await match_po(
        inv(
            line_items=lines,
            subtotal=sub,
            cgst=sub * D("0.09"),
            sgst=sub * D("0.09"),
            total=sub * D("1.18"),
        ),
        [p],
    )
    assert r.status == "matched", r.explanation
    assert [m.po_line for m in r.lines] == [1, 0]


async def test_identical_lots_at_same_price_paired_by_exact_quantity():
    """Regression (eval case-072): truncated lot lines at one price were paired greedily
    with the wrong quantities and reported as over-billing."""
    p = po(line("Guard shift (lot 1)", "4", "950.00"), line("Guard shift (lot 2)", "2", "950.00"))
    lines = [li("Guard shift (lot …", 2, D("950.00")), li("Guard shift (lot …", 4, D("950.00"))]
    sub = D("5700.00")
    r = await match_po(
        inv(
            line_items=lines,
            subtotal=sub,
            cgst=sub * D("0.09"),
            sgst=sub * D("0.09"),
            total=sub * D("1.18"),
        ),
        [p],
    )
    assert r.status == "matched", r.explanation
