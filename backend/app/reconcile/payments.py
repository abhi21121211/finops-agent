"""Invoice ↔ bank statement matching, tenant-wide. Plain code.

Payments are allocated in passes, most certain first:
  1. reference  – the narration or reference quotes invoice numbers
  2. exact      – one transfer to an identified vendor equals one invoice, gross or net of TDS
  3. combined   – one transfer equals the sum of 2–4 of that vendor's unpaid invoices
Then each invoice is settled: fully paid, paid with a TDS shortfall, part-paid or unpaid.

TDS (tax deducted at source) is withheld on the taxable value, so the payment is
total − rate% × subtotal for one of the common rates.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import combinations

from app.reconcile.po_match import normalize_ref
from app.schemas.reconcile import Allocation, PaymentMatch

TOLERANCE = Decimal("1.00")
TDS_RATES = tuple(Decimal(r) for r in ("0", "0.1", "1", "2", "5", "10"))
WINDOW_BEFORE = timedelta(days=7)  # advance payments
WINDOW_AFTER = timedelta(days=120)
MAX_COMBINED = 4
MIN_REF_LEN = 5  # shorter invoice numbers match too much narration noise

_GENERIC = {
    "PVT",
    "PRIVATE",
    "LTD",
    "LIMITED",
    "LLP",
    "CO",
    "COMPANY",
    "AND",
    "THE",
    "OF",
    "INDIA",
    "SERVICES",
    "SERVICE",
    "WORKS",
    "SUPPLIES",
    "SUPPLY",
    "TRADERS",
    "ENTERPRISES",
    "SHREE",
    "SHRI",
    "SRI",
    "NEW",
    "OFFICE",
    "CLOUD",
    "EVENT",
    "RENTALS",
    "PACKAGING",
}


@dataclass(frozen=True)
class OpenInvoice:
    id: str
    vendor_gstin: str | None
    vendor_name: str
    invoice_number: str
    invoice_date: date
    total: Decimal
    subtotal: Decimal


@dataclass(frozen=True)
class Txn:
    id: str
    date: date
    amount: Decimal  # money out, positive
    narration: str
    reference: str | None = None


def tds_amount(inv: OpenInvoice, rate: Decimal) -> Decimal:
    return (inv.subtotal * rate / 100).quantize(Decimal("0.01"))


def net_of_tds(inv: OpenInvoice, rate: Decimal) -> Decimal:
    return inv.total - tds_amount(inv, rate)


def vendor_key(name: str) -> str | None:
    """The first distinctive word of a vendor name (banks truncate narrations)."""
    for word in re.findall(r"[A-Z0-9]+", name.upper()):
        if len(word) >= 4 and word not in _GENERIC:
            return word
    return None


def _in_window(inv: OpenInvoice, txn: Txn) -> bool:
    return inv.invoice_date - WINDOW_BEFORE <= txn.date <= inv.invoice_date + WINDOW_AFTER


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOLERANCE


def _inr(x: Decimal) -> str:
    return f"₹{x:,.2f}"


class _Ledger:
    def __init__(self, invoices: list[OpenInvoice], txns: list[Txn]):
        self.invoices = sorted(invoices, key=lambda i: (i.invoice_date, i.invoice_number))
        self.txns = sorted(txns, key=lambda t: (t.date, t.id))
        self.left = {t.id: t.amount for t in self.txns}
        self.alloc: dict[str, list[Allocation]] = {i.id: [] for i in self.invoices}
        keys = {i.vendor_gstin or i.vendor_name: vendor_key(i.vendor_name) for i in self.invoices}
        self.vendor_keys = {k: v for k, v in keys.items() if v}

    def paid(self, inv: OpenInvoice) -> Decimal:
        return sum((a.amount for a in self.alloc[inv.id]), Decimal("0"))

    def give(self, inv: OpenInvoice, txn: Txn, amount: Decimal, kind: str) -> None:
        amount = min(amount, self.left[txn.id])
        if amount <= 0:
            return
        self.left[txn.id] -= amount
        self.alloc[inv.id].append(
            Allocation(
                transaction_id=txn.id,
                amount=amount,
                date=txn.date,
                narration=txn.narration,
                kind=kind,
            )
        )

    def vendor_of(self, txn: Txn) -> str | None:
        """Vendor (gstin or name) whose key word starts a narration word; None if 0 or >1."""
        words = re.findall(r"[A-Z0-9]+", f"{txn.narration} {txn.reference or ''}".upper())
        hits = {
            vendor
            for vendor, key in self.vendor_keys.items()
            if any(w.startswith(key) or (len(w) >= 5 and key.startswith(w)) for w in words)
        }
        return hits.pop() if len(hits) == 1 else None

    def unpaid_for(self, vendor: str, txn: Txn) -> list[OpenInvoice]:
        return [
            i
            for i in self.invoices
            if (i.vendor_gstin or i.vendor_name) == vendor
            and not self.alloc[i.id]
            and _in_window(i, txn)
        ]


def allocate_payments(invoices: list[OpenInvoice], txns: list[Txn]) -> dict[str, PaymentMatch]:
    ledger = _Ledger(invoices, txns)
    _reference_pass(ledger)
    _exact_pass(ledger)
    _combined_pass(ledger)
    return {inv.id: _settle(ledger, inv) for inv in ledger.invoices}


def _reference_pass(ledger: _Ledger) -> None:
    for txn in ledger.txns:
        hay = normalize_ref(f"{txn.narration} {txn.reference or ''}")
        refs = [
            i
            for i in ledger.invoices
            if len(normalize_ref(i.invoice_number)) >= MIN_REF_LEN
            and normalize_ref(i.invoice_number) in hay
        ]
        vendor = ledger.vendor_of(txn)
        if vendor and len({(i.vendor_gstin or i.vendor_name) for i in refs}) > 1:
            refs = [i for i in refs if (i.vendor_gstin or i.vendor_name) == vendor]
        if not refs:
            continue
        if len(refs) == 1:
            inv = refs[0]
            ledger.give(inv, txn, inv.total - ledger.paid(inv), "reference")
            continue
        # One transfer for several quoted invoices: find the TDS rate that explains it.
        for rate in TDS_RATES:
            dues = [net_of_tds(i, rate) - ledger.paid(i) for i in refs]
            if _close(sum(dues, Decimal("0")), txn.amount):
                for inv, due in zip(refs, dues, strict=True):
                    ledger.give(inv, txn, due, "combined")
                break
        else:
            for inv in refs:  # unexplained split: settle oldest first
                ledger.give(inv, txn, inv.total - ledger.paid(inv), "reference")


def _exact_pass(ledger: _Ledger) -> None:
    for txn in ledger.txns:
        if ledger.left[txn.id] != txn.amount:
            continue  # already (partly) used
        vendor = ledger.vendor_of(txn)
        if vendor is None:
            continue  # never match on amount alone: rent or salary can equal an invoice
        candidates = ledger.unpaid_for(vendor, txn)
        for rate in TDS_RATES:
            hits = [i for i in candidates if _close(net_of_tds(i, rate), txn.amount)]
            if hits:
                inv = hits[0]  # same amount twice (e.g. a monthly fee): oldest first
                ledger.give(inv, txn, txn.amount, "exact")
                break


def _combined_pass(ledger: _Ledger) -> None:
    for txn in ledger.txns:
        if ledger.left[txn.id] != txn.amount:
            continue
        vendor = ledger.vendor_of(txn)
        if vendor is None:
            continue
        candidates = ledger.unpaid_for(vendor, txn)[:12]
        found = _find_combination(candidates, txn.amount)
        if found:
            group, rate = found
            for inv in group:
                ledger.give(inv, txn, net_of_tds(inv, rate), "combined")


def _find_combination(
    candidates: list[OpenInvoice], amount: Decimal
) -> tuple[tuple[OpenInvoice, ...], Decimal] | None:
    for size in range(2, min(MAX_COMBINED, len(candidates)) + 1):
        for group in combinations(candidates, size):
            for rate in TDS_RATES:
                if _close(sum((net_of_tds(i, rate) for i in group), Decimal("0")), amount):
                    return group, rate
    return None


def _settle(ledger: _Ledger, inv: OpenInvoice) -> PaymentMatch:
    allocs = ledger.alloc[inv.id]
    if not allocs:
        return PaymentMatch(
            status="unpaid",
            outstanding=inv.total,
            explanation="No matching payment in the bank statements yet.",
        )
    paid = ledger.paid(inv)
    shortfall = inv.total - paid
    when = ", ".join(f"{d:%d %b %Y}" for d in sorted({a.date for a in allocs}))
    n = len(allocs)
    how = f"in {n} transfers" if n > 1 else "in one transfer"
    combined = [a for a in allocs if a.kind == "combined"]
    if combined:
        how += " covering several invoices"

    if _close(shortfall, Decimal("0")) or shortfall < 0:
        extra = f"; overpaid by {_inr(-shortfall)}" if shortfall < -TOLERANCE else ""
        return PaymentMatch(
            status="paid",
            paid=paid,
            outstanding=Decimal("0"),
            allocations=allocs,
            explanation=f"Paid {_inr(paid)} {how} ({when}){extra}.",
        )
    for rate in TDS_RATES[1:]:
        if _close(tds_amount(inv, rate), shortfall):
            return PaymentMatch(
                status="paid",
                paid=paid,
                tds_rate=rate,
                tds_amount=shortfall,
                outstanding=Decimal("0"),
                allocations=allocs,
                explanation=(
                    f"Paid {_inr(paid)} {how} ({when}). The {_inr(shortfall)} difference is "
                    f"{rate:g}% TDS on the taxable value {_inr(inv.subtotal)}."
                ),
            )
    return PaymentMatch(
        status="partial",
        paid=paid,
        outstanding=shortfall,
        allocations=allocs,
        explanation=(
            f"Part-paid: {_inr(paid)} received {how} ({when}); {_inr(shortfall)} outstanding."
        ),
    )
