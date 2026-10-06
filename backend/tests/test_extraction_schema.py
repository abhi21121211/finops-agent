from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.extraction import CONFIDENCE_FIELDS, ExtractedInvoice, ExtractionOutput
from tests.conftest import sample_extraction


def test_parses_indian_formatted_money():
    inv = ExtractedInvoice.model_validate(
        sample_extraction(subtotal="₹ 1,00,000.50", total="Rs. 1,18,000.59", cgst="")["invoice"]
    )
    assert inv.subtotal == Decimal("100000.50")
    assert inv.total == Decimal("118000.59")
    assert inv.cgst == Decimal("0")


def test_blank_strings_become_none():
    inv = ExtractedInvoice.model_validate(
        sample_extraction(po_number="N/A", vendor_gstin="", due_date="null")["invoice"]
    )
    assert inv.po_number is None
    assert inv.vendor_gstin is None
    assert inv.due_date is None


def test_bank_account_keeps_only_last4():
    inv = ExtractedInvoice.model_validate(
        sample_extraction(bank_account_last4="XXXX XXXX 9876 5432")["invoice"]
    )
    assert inv.bank_account_last4 == "5432"


def test_gstin_normalised():
    data = sample_extraction(vendor_gstin="27abccd 1234e1z5")["invoice"]
    inv = ExtractedInvoice.model_validate(data)
    assert inv.vendor_gstin == "27ABCCD1234E1Z5"


def test_missing_mandatory_field_rejected():
    data = sample_extraction()["invoice"]
    del data["invoice_number"]
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate(data)


def test_confidence_clamped_and_filled():
    out = ExtractionOutput.model_validate(
        {**sample_extraction(), "confidence": {"total": 1.7, "vendor_name": -1, "bogus": 0.9}}
    )
    assert set(out.confidence) == set(CONFIDENCE_FIELDS)
    assert out.confidence["total"] == 1.0
    assert out.confidence["vendor_name"] == 0.0
    assert out.confidence["invoice_number"] == 0.5  # missing → unsure
    assert "bogus" not in out.confidence
