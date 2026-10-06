"""Invoice ↔ purchase order matching. Plain code; an optional `judge` callback (an LLM) is
consulted only for line descriptions that are neither clearly the same nor clearly
different."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher

from app.schemas.extraction import ExtractedInvoice
from app.schemas.reconcile import LineMatch, LineStatus, POMatch

PRICE_TOLERANCE_PCT = 2.0
AMOUNT_TOLERANCE = Decimal("1.00")
SURE_MATCH = 0.75  # similarity at or above: same item without asking
MAYBE_MATCH = 0.10  # leftover pairs at or above this go to the judge, if any

# (invoice description, PO description) -> same item?
Judge = Callable[[list[tuple[str, str]]], Awaitable[list[bool]]]


@dataclass
class POLineData:
    description: str
    quantity: Decimal
    unit_price: Decimal
    billed_before: Decimal = Decimal("0")  # quantity billed by other live invoices


@dataclass
class POData:
    po_id: str
    po_number: str
    vendor_gstin: str | None
    vendor_name: str
    total: Decimal
    status: str = "open"
    lines: list[POLineData] = field(default_factory=list)


def normalize_ref(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


_STOP = {"OF", "THE", "AND", "FOR", "WITH", "X", "IN", "MM", "M", "PCS", "NOS", "BOX"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t.upper() not in _STOP}


def similarity(a: str, b: str) -> float:
    """Max of token overlap and character similarity, both 0..1."""
    ta, tb = _tokens(a), _tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    chars = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return round(max(jaccard, chars), 3)


def find_po(inv: ExtractedInvoice, pos: list[POData]) -> tuple[POData | None, str]:
    """By PO number; otherwise an open PO from the same vendor whose value equals the
    invoice's taxable value."""
    if inv.po_number:
        want = normalize_ref(inv.po_number)
        for po in pos:
            if normalize_ref(po.po_number) == want:
                return po, "po_number"
        return None, "none"
    same_vendor = [
        po
        for po in pos
        if po.status == "open" and inv.vendor_gstin and po.vendor_gstin == inv.vendor_gstin
    ]
    by_amount = [po for po in same_vendor if abs(po.total - inv.subtotal) <= AMOUNT_TOLERANCE]
    if len(by_amount) == 1:
        return by_amount[0], "vendor_amount"
    return None, "none"


async def match_po(inv: ExtractedInvoice, pos: list[POData], judge: Judge | None = None) -> POMatch:
    po, found_by = find_po(inv, pos)
    if po is None:
        if inv.po_number:
            why = f"Invoice cites {inv.po_number}, but no such purchase order exists."
        else:
            why = "Invoice has no PO number and no open PO from this vendor has the same value."
        return POMatch(status="no_po", explanation=why)

    head = f"{po.po_number}"
    if found_by == "vendor_amount":
        head += " (found by vendor and amount; the invoice has no PO number)"

    if inv.vendor_gstin and po.vendor_gstin and inv.vendor_gstin != po.vendor_gstin:
        return POMatch(
            status="mismatch",
            po_id=po.po_id,
            po_number=po.po_number,
            found_by=found_by,
            explanation=f"{head} was issued to {po.vendor_name}, not to this invoice's vendor.",
        )
    if po.status != "open":
        return POMatch(
            status="mismatch",
            po_id=po.po_id,
            po_number=po.po_number,
            found_by=found_by,
            explanation=f"{head} is {po.status}; it cannot be billed.",
        )

    pairs = await _assign_lines(inv, po, judge)
    lines = [_compare(i, inv, po, pairs.get(i)) for i in range(len(inv.line_items))]
    bad = [lm for lm in lines if lm.status != LineStatus.ok]
    status = "mismatch" if bad else "matched"
    if bad:
        explanation = f"{head}: " + " ".join(lm.note for lm in bad)
    else:
        n = len(lines)
        explanation = (
            f"{head}: all {n} line{'s' if n != 1 else ''} match the PO "
            f"(price within {PRICE_TOLERANCE_PCT:g}%, quantity within what is left to bill)."
        )
    return POMatch(
        status=status,
        po_id=po.po_id,
        po_number=po.po_number,
        found_by=found_by,
        lines=lines,
        explanation=explanation,
    )


async def _assign_lines(
    inv: ExtractedInvoice, po: POData, judge: Judge | None
) -> dict[int, tuple[int, float, str]]:
    """Greedy one-to-one assignment of invoice lines to PO lines, best similarity first."""

    def rank(i: int, j: int) -> tuple[float, float, int, int]:
        li, pl = inv.line_items[i], po.lines[j]
        sim = similarity(li.description, pl.description)
        # Tie-break near-identical descriptions (truncated "… (lot 2)" lines, colour
        # variants) by whether price and quantity also agree. The bonus is small, so a
        # clearly better description still wins and a wrong price is still reported.
        tolerance = Decimal(str(PRICE_TOLERANCE_PCT))
        diff_pct = abs(li.unit_price - pl.unit_price) / pl.unit_price * 100 if pl.unit_price else 0
        price_ok = diff_pct <= tolerance
        available = pl.quantity - pl.billed_before
        qty_ok = li.quantity <= available
        qty_exact = li.quantity == available  # billing exactly what is left: likely its line
        bonus = 0.15 * bool(price_ok) + 0.05 * bool(qty_ok) + 0.04 * bool(qty_exact)
        return (sim + bonus, sim, i, j)

    scored = sorted(
        (rank(i, j) for i in range(len(inv.line_items)) for j in range(len(po.lines))),
        reverse=True,
    )
    pairs: dict[int, tuple[int, float, str]] = {}
    used_po: set[int] = set()
    for _, score, i, j in scored:
        if score >= SURE_MATCH and i not in pairs and j not in used_po:
            pairs[i] = (j, score, "text")
            used_po.add(j)

    # Leftover lines may be the same item worded differently ("CC388A black toner" vs
    # "HP 88A toner cartridge"). Ask the judge about each one's best remaining PO line.
    if judge:
        candidates: list[tuple[float, int, int]] = []
        taken: set[int] = set(used_po)
        for _, score, i, j in scored:
            if i in pairs or j in taken or score < MAYBE_MATCH:
                continue
            if any(c[1] == i for c in candidates):
                continue
            candidates.append((score, i, j))
            taken.add(j)
        if candidates:
            verdicts = await judge(
                [(inv.line_items[i].description, po.lines[j].description) for _, i, j in candidates]
            )
            for (score, i, j), same in zip(candidates, verdicts, strict=True):
                if same:
                    pairs[i] = (j, score, "llm")
                    used_po.add(j)
    return pairs


def _compare(
    i: int, inv: ExtractedInvoice, po: POData, pair: tuple[int, float, str] | None
) -> LineMatch:
    li = inv.line_items[i]
    if pair is None:
        return LineMatch(
            invoice_line=i,
            po_line=None,
            invoice_description=li.description,
            po_description=None,
            similarity=0.0,
            matched_by="none",
            quantity=li.quantity,
            quantity_available=None,
            unit_price=li.unit_price,
            po_unit_price=None,
            price_diff_pct=None,
            status=LineStatus.unmatched,
            note=f"“{li.description}” is not on the PO.",
        )
    j, score, how = pair
    pl = po.lines[j]
    available = pl.quantity - pl.billed_before
    diff_pct = (
        float((li.unit_price - pl.unit_price) / pl.unit_price * 100) if pl.unit_price else 0.0
    )
    if abs(diff_pct) > PRICE_TOLERANCE_PCT:
        status = LineStatus.price
        note = (
            f"“{li.description}” is billed at {li.unit_price:.2f} vs PO {pl.unit_price:.2f} "
            f"({diff_pct:+.1f}%)."
        )
    elif li.quantity > available:
        status = LineStatus.quantity
        before = f" ({pl.billed_before:g} already billed)" if pl.billed_before else ""
        note = (
            f"“{li.description}” bills {li.quantity:g}, but only {available:g} of "
            f"{pl.quantity:g} remain on the PO{before}."
        )
    else:
        status = LineStatus.ok
        note = "ok"
    return LineMatch(
        invoice_line=i,
        po_line=j,
        invoice_description=li.description,
        po_description=pl.description,
        similarity=score,
        matched_by=how,
        quantity=li.quantity,
        quantity_available=available,
        unit_price=li.unit_price,
        po_unit_price=pl.unit_price,
        price_diff_pct=round(diff_pct, 2),
        status=status,
        note=note,
    )
