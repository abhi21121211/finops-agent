from datetime import date
from decimal import Decimal as D

import pytest

from app.reconcile.bank_csv import StatementError, parse_statement

HDFC_STYLE = b"""HDFC BANK Ltd.,,,,,,
Statement of account,,,,,,
Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
25/09/26,NEFT/HDFC/KAVERI OFFICE SUPPL,N268201,25/09/26,"24,221.50",,"4,75,778.50"
26/09/26,SALARY CREDIT REVERSAL,,26/09/26,,"1,000.00","4,76,778.50"
,,,,,,
Closing balance,,,,,,"4,76,778.50"
"""

SIGNED_STYLE = b"""Transaction Date,Description,Reference,Amount,Type
2026-10-02,RTGS NILGIRI,UTR123,21040.00,DR
2026-10-03,INTEREST,,12.50,CR
"""


def test_hdfc_layout_with_preamble_and_footer():
    rows = parse_statement(HDFC_STYLE)
    assert [(r.date, r.amount) for r in rows] == [
        (date(2026, 9, 25), D("-24221.50")),
        (date(2026, 9, 26), D("1000.00")),
    ]
    assert rows[0].reference == "N268201"


def test_signed_amount_with_type_column():
    rows = parse_statement(SIGNED_STYLE)
    assert [r.amount for r in rows] == [D("-21040.00"), D("12.50")]


def test_identical_rows_get_distinct_fingerprints_but_reupload_is_stable():
    data = (
        b"Date,Narration,Debit,Credit\n01/10/2026,NEFT KAVERI,100,\n01/10/2026,NEFT KAVERI,100,\n"
    )
    first, second = parse_statement(data)
    assert first.fingerprint != second.fingerprint
    assert [r.fingerprint for r in parse_statement(data)] == [first.fingerprint, second.fingerprint]


def test_missing_columns_explained():
    with pytest.raises(StatementError, match="No header row"):
        parse_statement(b"foo,bar\n1,2\n")


def test_bad_date_reports_row_number():
    with pytest.raises(StatementError, match="row 2"):
        parse_statement(b"Date,Narration,Debit,Credit\n31/31/2026,X,10,\n")
