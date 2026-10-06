"""Deterministic invoice checks (spec §4, "Validation tools"). No LLM here: maths and
formats are decided by code. Checks that need data (duplicates, vendor master) take it as
arguments so they stay pure and testable."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.agents.tools.gstin import gstin_problem
from app.schemas.extraction import ExtractedInvoice
from app.schemas.validation import Severity, ValidationIssue

TOLERANCE = Decimal("1.00")  # rupees


def _issue(
    check: str,
    field: str | None,
    message: str,
    *,
    fixable: bool,
    severity: Severity = Severity.error,
) -> ValidationIssue:
    return ValidationIssue(
        check=check, field=field, severity=severity, message=message, fixable=fixable
    )


def check_gstin_format(inv: ExtractedInvoice) -> list[ValidationIssue]:
    issues = []
    for field in ("vendor_gstin", "buyer_gstin"):
        value = getattr(inv, field)
        if value is None:
            if field == "vendor_gstin":
                issues.append(
                    _issue("gstin_format", field, "Vendor GSTIN is missing", fixable=True)
                )
            continue
        if problem := gstin_problem(value):
            issues.append(_issue("gstin_format", field, f"{value}: {problem}", fixable=True))
    return issues


def check_tax_maths(inv: ExtractedInvoice) -> list[ValidationIssue]:
    issues = []
    for i, li in enumerate(inv.line_items):
        expected = li.quantity * li.unit_price
        if abs(expected - li.amount) > TOLERANCE:
            issues.append(
                _issue(
                    "tax_maths",
                    f"line_items[{i}].amount",
                    f"Line {i + 1}: {li.quantity} × {li.unit_price:.2f} = {expected:.2f}, "
                    f"but amount is {li.amount:.2f}",
                    fixable=True,
                )
            )

    lines_sum = sum((li.amount for li in inv.line_items), Decimal(0))
    if inv.line_items and abs(lines_sum - inv.subtotal) > TOLERANCE:
        issues.append(
            _issue(
                "tax_maths",
                "subtotal",
                f"Line amounts sum to {lines_sum:.2f}, but subtotal is {inv.subtotal:.2f}",
                fixable=True,
            )
        )

    taxes = inv.cgst + inv.sgst + inv.igst
    if abs(inv.subtotal + taxes - inv.total) > TOLERANCE:
        issues.append(
            _issue(
                "tax_maths",
                "total",
                f"Subtotal {inv.subtotal:.2f} + taxes {taxes:.2f} = {inv.subtotal + taxes:.2f}, "
                f"but total is {inv.total:.2f}",
                fixable=True,
            )
        )

    expected_tax = sum((li.amount * li.tax_rate / 100 for li in inv.line_items), Decimal(0))
    if inv.line_items and abs(expected_tax - taxes) > TOLERANCE:
        issues.append(
            _issue(
                "tax_maths",
                "cgst" if inv.cgst or inv.sgst else "igst",
                f"Line GST rates imply {expected_tax:.2f} tax, but the invoice shows {taxes:.2f}",
                fixable=True,
            )
        )
    return issues


def check_tax_split(inv: ExtractedInvoice) -> list[ValidationIssue]:
    intra = inv.cgst > 0 or inv.sgst > 0
    inter = inv.igst > 0
    issues = []
    if intra and inter:
        issues.append(
            _issue("tax_split", "igst", "Both CGST/SGST and IGST are charged", fixable=True)
        )
    if intra and abs(inv.cgst - inv.sgst) > TOLERANCE:
        issues.append(
            _issue(
                "tax_split",
                "sgst",
                f"CGST ({inv.cgst:.2f}) and SGST ({inv.sgst:.2f}) should be equal",
                fixable=True,
            )
        )
    # Place of supply: same state code → CGST+SGST, different → IGST.
    if inv.vendor_gstin and inv.buyer_gstin and (intra or inter):
        same_state = inv.vendor_gstin[:2] == inv.buyer_gstin[:2]
        if same_state and inter and not intra:
            issues.append(
                _issue(
                    "tax_split",
                    "igst",
                    "Vendor and buyer are in the same state, so CGST + SGST is expected, not IGST",
                    fixable=True,
                    severity=Severity.warning,
                )
            )
        if not same_state and intra and not inter:
            issues.append(
                _issue(
                    "tax_split",
                    "cgst",
                    "Vendor and buyer are in different states, so IGST is expected, "
                    "not CGST + SGST",
                    fixable=True,
                    severity=Severity.warning,
                )
            )
    return issues


def check_dates(inv: ExtractedInvoice, today: date) -> list[ValidationIssue]:
    issues = []
    if inv.invoice_date > today:
        issues.append(
            _issue(
                "dates",
                "invoice_date",
                f"Invoice date {inv.invoice_date} is in the future",
                fixable=True,
            )
        )
    if inv.due_date and inv.due_date < inv.invoice_date:
        issues.append(
            _issue(
                "dates",
                "due_date",
                f"Due date {inv.due_date} is before invoice date {inv.invoice_date}",
                fixable=True,
            )
        )
    return issues


@dataclass(frozen=True)
class ExistingInvoice:
    invoice_id: str
    vendor_gstin: str | None
    invoice_number: str


def check_duplicate(inv: ExtractedInvoice, others: list[ExistingInvoice]) -> list[ValidationIssue]:
    """Same vendor GSTIN and invoice number as another live invoice of this tenant."""
    if not inv.vendor_gstin:
        return []
    number = inv.invoice_number.strip().upper()
    for o in others:
        if o.vendor_gstin == inv.vendor_gstin and o.invoice_number.strip().upper() == number:
            return [
                _issue(
                    "duplicate",
                    "invoice_number",
                    f"Invoice {inv.invoice_number} from this vendor was already received "
                    f"(invoice {o.invoice_id})",
                    fixable=False,
                )
            ]
    return []


def check_vendor_known(inv: ExtractedInvoice, known_gstins: set[str]) -> list[ValidationIssue]:
    if not inv.vendor_gstin or gstin_problem(inv.vendor_gstin):
        return []  # already reported by check_gstin_format
    if inv.vendor_gstin not in known_gstins:
        return [
            _issue(
                "vendor_known",
                "vendor_gstin",
                f"{inv.vendor_name} ({inv.vendor_gstin}) is not in the vendor master",
                fixable=False,
            )
        ]
    return []


def run_all(
    inv: ExtractedInvoice,
    *,
    today: date,
    others: list[ExistingInvoice],
    known_gstins: set[str],
) -> list[ValidationIssue]:
    return [
        *check_gstin_format(inv),
        *check_tax_maths(inv),
        *check_tax_split(inv),
        *check_dates(inv, today),
        *check_duplicate(inv, others),
        *check_vendor_known(inv, known_gstins),
    ]
