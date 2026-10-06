from datetime import date

import pytest

from app.agents.tools.gstin import gstin_problem
from app.agents.tools.validation import (
    ExistingInvoice,
    check_dates,
    check_duplicate,
    check_gstin_format,
    check_tax_maths,
    check_tax_split,
    check_vendor_known,
    run_all,
)
from app.demo_data import BUYER_GSTIN, KAVERI, KNOWN_VENDORS, NILGIRI, ZEPHYR
from app.schemas.extraction import ExtractedInvoice
from app.schemas.validation import Severity
from tests.conftest import sample_extraction

TODAY = date(2026, 10, 6)


def inv(**overrides) -> ExtractedInvoice:
    return ExtractedInvoice.model_validate(sample_extraction(**overrides)["invoice"])


def checks(issues) -> list[str]:
    return [i.check for i in issues]


def test_clean_invoice_passes_everything():
    assert run_all(inv(), today=TODAY, others=[], known_gstins={KAVERI.gstin}) == []


# ── GSTIN ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("gstin", [v.gstin for v in (*KNOWN_VENDORS, ZEPHYR)] + [BUYER_GSTIN])
def test_demo_gstins_are_valid(gstin):
    assert gstin_problem(gstin) is None


@pytest.mark.parametrize(
    ("gstin", "reason"),
    [
        ("27KAVCO4821K1Z", "15 characters"),
        ("27KAVCO4821K1ZJX", "15 characters"),
        ("27KAVC04821K1ZJ", "pattern"),  # zero instead of O in the PAN
        ("27KAVCO4821K1YJ", "pattern"),  # 14th char must be Z
        ("27KAVCO4821K0ZJ", "pattern"),  # entity number cannot be 0
        ("45KAVCO4821K1ZJ", "state code"),
        ("27KAVCO4821K1ZK", "check character"),
    ],
)
def test_bad_gstins_explain_why(gstin, reason):
    assert reason in gstin_problem(gstin)


def test_every_single_character_misread_is_caught():
    """The mod-36 check character detects any one-character substitution (a typical
    OCR misread such as O↔0 or 8↔B), at every position."""
    good = KAVERI.gstin
    for pos in range(15):
        for ch in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            if ch != good[pos]:
                bad = good[:pos] + ch + good[pos + 1 :]
                assert gstin_problem(bad) is not None, bad


def test_missing_vendor_gstin_is_fixable_error():
    [issue] = check_gstin_format(inv(vendor_gstin=None))
    assert issue.field == "vendor_gstin" and issue.fixable and issue.severity == Severity.error


def test_missing_buyer_gstin_is_fine():
    assert check_gstin_format(inv(buyer_gstin=None)) == []


# ── Tax maths ──────────────────────────────────────────────────────────


def test_within_one_rupee_rounding_passes():
    assert check_tax_maths(inv(total="1180.99", cgst="90.40", sgst="90.40")) == []


def test_total_mismatch_flagged():
    issues = check_tax_maths(inv(total=1330))
    assert [i.field for i in issues] == ["total"]
    assert "1180.00" in issues[0].message


def test_line_amount_not_qty_times_price():
    line = {**sample_extraction()["invoice"]["line_items"][0], "amount": 900}
    issues = check_tax_maths(inv(line_items=[line], subtotal=900, total=1062, cgst=81, sgst=81))
    assert "line_items[0].amount" in [i.field for i in issues]


def test_lines_do_not_sum_to_subtotal():
    issues = check_tax_maths(inv(subtotal=1100, total=1280))
    assert "subtotal" in [i.field for i in issues]


def test_tax_does_not_match_line_rates():
    # Internally consistent total, but 12% charged on 18% lines.
    issues = check_tax_maths(inv(cgst=60, sgst=60, total=1120))
    assert [i.field for i in issues] == ["cgst"]


def test_mixed_rates_compute_per_line():
    lines = [
        {"description": "A", "quantity": 1, "unit_price": 1000, "tax_rate": 12, "amount": 1000},
        {"description": "B", "quantity": 2, "unit_price": 500, "tax_rate": 18, "amount": 1000},
    ]
    assert (
        check_tax_maths(inv(line_items=lines, subtotal=2000, cgst=150, sgst=150, total=2300)) == []
    )


def test_sample_04_misprint_is_caught():
    lines = [
        {
            "description": "Markers",
            "quantity": 10,
            "unit_price": 310,
            "tax_rate": 18,
            "amount": 3100,
        },
        {
            "description": "Stapler",
            "quantity": 4,
            "unit_price": 640,
            "tax_rate": 18,
            "amount": 2560,
        },
    ]
    bad = inv(line_items=lines, subtotal=5660, cgst="659.40", sgst="509.40", total="6678.80")
    assert {i.check for i in run_all(bad, today=TODAY, others=[], known_gstins={KAVERI.gstin})} == {
        "tax_maths",
        "tax_split",
    }


# ── Tax split ──────────────────────────────────────────────────────────


def test_cgst_and_igst_together_is_error():
    issues = check_tax_split(inv(igst=180, total=1360))
    assert any(i.severity == Severity.error and i.field == "igst" for i in issues)


def test_unequal_cgst_sgst():
    assert checks(check_tax_split(inv(cgst=100, sgst=80))) == ["tax_split"]


def test_interstate_vendor_charging_cgst_is_warning_only():
    issues = check_tax_split(inv(vendor_gstin=NILGIRI.gstin))  # 29 → 27, but CGST+SGST
    assert [i.severity for i in issues] == [Severity.warning]


def test_interstate_igst_passes():
    assert check_tax_split(inv(vendor_gstin=NILGIRI.gstin, cgst=0, sgst=0, igst=180)) == []


def test_zero_rated_invoice_has_no_split_issue():
    line = {**sample_extraction()["invoice"]["line_items"][0], "tax_rate": 0}
    assert check_tax_split(inv(line_items=[line], cgst=0, sgst=0, total=1000)) == []


# ── Dates ──────────────────────────────────────────────────────────────


def test_future_invoice_date():
    assert checks(check_dates(inv(invoice_date="2026-12-01", due_date=None), TODAY)) == ["dates"]


def test_due_before_invoice_date():
    [issue] = check_dates(inv(due_date="2026-09-01"), TODAY)
    assert issue.field == "due_date"


def test_same_day_due_date_ok():
    assert check_dates(inv(due_date="2026-09-15"), TODAY) == []


# ── Duplicates and vendor master (not fixable by re-reading) ───────────


def test_duplicate_matches_case_and_whitespace_insensitively():
    others = [ExistingInvoice("abc", KAVERI.gstin, " kav/26-27/001 ")]
    [issue] = check_duplicate(inv(), others)
    assert not issue.fixable and "abc" in issue.message


def test_same_number_other_vendor_is_not_duplicate():
    assert check_duplicate(inv(), [ExistingInvoice("abc", NILGIRI.gstin, "KAV/26-27/001")]) == []


def test_unknown_vendor_not_fixable():
    [issue] = check_vendor_known(inv(vendor_gstin=ZEPHYR.gstin, vendor_name=ZEPHYR.name), set())
    assert not issue.fixable and ZEPHYR.name in issue.message


def test_invalid_gstin_not_double_reported_as_unknown_vendor():
    assert check_vendor_known(inv(vendor_gstin="27KAVCO4821K1ZK"), set()) == []


def test_rate_check_skipped_when_invoice_prints_no_line_rates():
    """Regression: half the templates print no per-line GST rate; a guessed rate made the
    check reject correct invoices."""
    line = {**sample_extraction()["invoice"]["line_items"][0], "tax_rate": None}
    assert check_tax_maths(inv(line_items=[line])) == []


def test_rate_check_still_runs_when_every_line_has_a_rate():
    line = {**sample_extraction()["invoice"]["line_items"][0], "tax_rate": 12}
    assert [i.field for i in check_tax_maths(inv(line_items=[line]))] == ["cgst"]
