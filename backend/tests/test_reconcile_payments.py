from datetime import date
from decimal import Decimal as D

from app.reconcile.payments import OpenInvoice, Txn, allocate_payments, vendor_key

KAV = "27KAVCO4821K1ZJ"
NIL = "29NILCC7310N1ZD"


def inv(
    id,
    number,
    total,
    subtotal,
    *,
    vendor=KAV,
    name="Kaveri Office Supplies LLP",
    day=date(2026, 9, 10),
):
    return OpenInvoice(id, vendor, name, number, day, D(total), D(subtotal))


def txn(id, amount, narration, *, day=date(2026, 9, 25), ref=None):
    return Txn(id, day, D(amount), narration, ref)


A = inv("a", "KAV/26-27/1012", "17542.70", "15365.00")
B = inv("b", "KAV/26-27/1019", "6678.80", "5660.00")
N = inv(
    "n", "NIL/26-27/2045", "51920.00", "44000.00", vendor=NIL, name="Nilgiri Cloud Services Pvt Ltd"
)


def test_vendor_key_skips_generic_words():
    assert vendor_key("Kaveri Office Supplies LLP") == "KAVERI"
    assert vendor_key("Shree Ganesh Packaging Works") == "GANESH"
    assert vendor_key("Nilgiri Cloud Services Pvt Ltd") == "NILGIRI"


def test_no_payment_is_unpaid():
    r = allocate_payments([A], [])["a"]
    assert r.status == "unpaid" and r.outstanding == D("17542.70")


def test_exact_payment_by_vendor_and_amount():
    r = allocate_payments([A], [txn("t1", "17542.70", "NEFT/HDFC/KAVERI OFFICE SUPPL")])["a"]
    assert r.status == "paid" and r.allocations[0].kind == "exact"


def test_amount_alone_never_matches():
    """A transfer with no vendor or invoice reference is not assumed to pay an invoice."""
    r = allocate_payments([A], [txn("t1", "17542.70", "NEFT/RENT SEPTEMBER")])["a"]
    assert r.status == "unpaid"


def test_reference_match_works_with_formatting_differences():
    r = allocate_payments([A], [txn("t1", "17542.70", "IMPS PAYMENT KAV-26-27-1012")])["a"]
    assert r.status == "paid" and r.allocations[0].kind == "reference"


def test_tds_deduction_recognised():
    # 2% TDS on taxable value 15,365.00 = 307.30 → paid 17,235.40
    r = allocate_payments([A], [txn("t1", "17235.40", "NEFT KAVERI OFFICE")])["a"]
    assert r.status == "paid"
    assert r.tds_rate == D("2") and r.tds_amount == D("307.30")
    assert "2% TDS" in r.explanation


def test_tds_at_ten_percent_with_reference():
    r = allocate_payments([N], [txn("t1", "47520.00", "RTGS NIL/26-27/2045")])["n"]
    assert r.status == "paid" and r.tds_rate == D("10")


def test_part_payment_then_balance():
    first = txn("t1", "30000.00", "NEFT NILGIRI CLOUD NIL-26-27-2045 PART 1", day=date(2026, 9, 20))
    only_first = allocate_payments([N], [first])["n"]
    assert only_first.status == "partial"
    assert only_first.outstanding == D("21920.00")

    second = txn("t2", "21040.00", "NEFT NILGIRI CLOUD NIL-26-27-2045 BAL", day=date(2026, 10, 2))
    both = allocate_payments([N], [first, second])["n"]
    # 51,040 received; the 880 gap is 2% TDS on 44,000.
    assert both.status == "paid" and both.tds_rate == D("2")
    assert len(both.allocations) == 2


def test_partial_with_unexplained_gap_stays_partial():
    r = allocate_payments([N], [txn("t1", "40000.00", "NEFT NIL/26-27/2045")])["n"]
    assert r.status == "partial" and r.outstanding == D("11920.00")


def test_combined_payment_without_references():
    pay = txn("t1", "24221.50", "NEFT/HDFC/KAVERI OFFICE SUPPL/SEP BILLS")
    r = allocate_payments([A, B], [pay])
    assert r["a"].status == r["b"].status == "paid"
    assert {x.kind for x in r["a"].allocations} == {"combined"}
    assert "several invoices" in r["a"].explanation


def test_combined_payment_net_of_tds():
    # 2% TDS on 15,365 + 5,660 = 420.50 → 24,221.50 − 420.50
    r = allocate_payments([A, B], [txn("t1", "23801.00", "NEFT KAVERI OFFICE")])
    assert r["a"].tds_rate == r["b"].tds_rate == D("2")


def test_combined_payment_quoting_both_invoice_numbers():
    pay = txn("t1", "24221.50", "NEFT KAVERI KAV/26-27/1012 KAV/26-27/1019")
    r = allocate_payments([A, B], [pay])
    assert r["a"].paid == D("17542.70") and r["b"].paid == D("6678.80")


def test_combined_payment_ignores_other_vendors_invoices():
    other = inv("x", "NIL/26-27/9999", "6678.80", "5660.00", vendor=NIL, name="Nilgiri Cloud")
    r = allocate_payments([A, other, B], [txn("t1", "24221.50", "NEFT KAVERI OFFICE")])
    assert r["x"].status == "unpaid"
    assert r["a"].status == r["b"].status == "paid"


def test_same_amount_twice_pays_oldest_first():
    old = inv("old", "KAV/26-27/0001", "1180.00", "1000.00", day=date(2026, 8, 1))
    new = inv("new", "KAV/26-27/0002", "1180.00", "1000.00", day=date(2026, 9, 1))
    r = allocate_payments([new, old], [txn("t1", "1180.00", "NEFT KAVERI")])
    assert r["old"].status == "paid" and r["new"].status == "unpaid"


def test_one_transfer_is_not_counted_twice():
    twin = inv("twin", "KAV/26-27/1013", "17542.70", "15365.00")
    r = allocate_payments([A, twin], [txn("t1", "17542.70", "NEFT KAVERI")])
    assert sorted(x.status for x in r.values()) == ["paid", "unpaid"]


def test_payment_outside_date_window_ignored():
    early = txn("t1", "17542.70", "NEFT KAVERI", day=date(2026, 6, 1))
    assert allocate_payments([A], [early])["a"].status == "unpaid"


def test_ambiguous_vendor_narration_not_matched_by_amount():
    shared = inv("s", "NIL/26-27/7777", "17542.70", "15365.00", vendor=NIL, name="Nilgiri Cloud")
    r = allocate_payments([A, shared], [txn("t1", "17542.70", "NEFT KAVERI NILGIRI")])
    assert r["a"].status == r["s"].status == "unpaid"


def test_overpayment_reported():
    r = allocate_payments([A], [txn("t1", "18000.00", "NEFT KAV/26-27/1012")])["a"]
    # The reference pass caps at the invoice total; the extra stays unallocated.
    assert r.status == "paid" and r.paid == D("17542.70")


def test_dates_listed_chronologically():
    first = txn("t1", "30000.00", "NEFT NIL/26-27/2045", day=date(2026, 9, 20))
    second = txn("t2", "21040.00", "RTGS NIL/26-27/2045", day=date(2026, 10, 3))
    r = allocate_payments([N], [second, first])["n"]
    assert "(20 Sep 2026, 03 Oct 2026)" in r.explanation


def test_invoice_number_must_match_whole_tokens():
    from app.reconcile.payments import mentions

    assert mentions("NEFT/OPALINE/OT/26-27/4112", "OT/26-27/4112")
    assert mentions("IMPS KAV-26-27-1012 SEP", "KAV/26-27/1012")
    assert not mentions("NEFT/OPALINE TEXTILES/TDS REF00018", "#00018")
    assert not mentions("NEFT KAV/26-27/10123", "KAV/26-27/1012")  # longer number


def test_numeric_invoice_number_not_matched_inside_bank_reference():
    numeric = inv("num", "00018", "1180.00", "1000.00")
    t = Txn("t1", date(2026, 9, 25), D("2661.12"), "NEFT/KAVERI/TDS 2%", "REF00018")
    assert allocate_payments([numeric], [t])["num"].status == "unpaid"


def test_invoice_without_gstin_does_not_split_its_vendor():
    """Regression: a GSTIN-less invoice made 'Kaveri' two vendors, so every narration naming
    Kaveri looked ambiguous and no payment was matched."""
    no_gstin = OpenInvoice(
        "ng",
        None,
        "Kaveri Office Supplies LLP",
        "KAV/26-27/2000",
        date(2026, 9, 1),
        D("500.00"),
        D("423.73"),
    )
    r = allocate_payments([A, no_gstin], [txn("t1", "17542.70", "NEFT KAVERI OFFICE")])
    assert r["a"].status == "paid"
