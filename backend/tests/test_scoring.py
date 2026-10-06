from app.evals.scoring import (
    CaseResult,
    aggregate,
    f1,
    gate,
    line_item_counts,
    normalize,
    score_fields,
)


def test_normalisation():
    assert normalize("total", "1180") == "1180.00"
    assert normalize("total", 1180) == normalize("total", "1180.00")
    assert normalize("invoice_date", "2026-09-15T00:00:00") == "2026-09-15"
    assert normalize("vendor_name", "Kaveri  Office Supplies LLP.") == normalize(
        "vendor_name", "kaveri office supplies llp"
    )
    assert normalize("vendor_gstin", " 27kavco4821k1zj ") == "27KAVCO4821K1ZJ"
    assert normalize("po_number", "") is None


def test_field_scores_and_missing_extraction():
    expected = {"total": "1180.00", "vendor_name": "A", "po_number": None}
    scores = {
        s.field: s.correct for s in score_fields(expected, {"total": 1180, "vendor_name": "a"})
    }
    assert scores["total"] and scores["vendor_name"] and scores["po_number"]
    assert not any(s.correct for s in score_fields(expected, None) if s.expected)


def test_line_items_f1():
    e = [
        {"description": "Widget", "quantity": "2", "unit_price": "500", "amount": "1000"},
        {"description": "Gadget", "quantity": "1", "unit_price": "10", "amount": "10"},
    ]
    a = [{"description": "Widget", "quantity": 2, "unit_price": 500, "amount": 1000}]
    tp, p, x = line_item_counts(e, a)
    assert (tp, p, x) == (1, 1, 2)
    assert round(f1(tp, p, x), 4) == 0.6667


def _case(pred, exp, requires=()):
    return CaseResult("c", "clean", list(requires), exp, pred, None, None)


def test_gate_ignores_deferred_false_approvals_but_not_others():
    deferred = aggregate([_case("auto_approve", "human_review", ["anomaly"])])
    assert gate(deferred, None) == []
    real = aggregate([_case("auto_approve", "human_review")])
    assert gate(real, None) == ["1 false auto-approval(s)"]


def test_gate_accuracy_drop():
    m = {"false_auto_approvals": 0, "field_accuracy": 0.90}
    assert gate(m, {"field_accuracy": 0.915}) == []
    assert "below baseline" in gate(m, {"field_accuracy": 0.93})[0]
