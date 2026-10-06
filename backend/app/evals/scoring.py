"""Scoring for the evaluation harness (spec §8 "Metrics"). Pure functions."""

import re
import statistics
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.reconcile.po_match import similarity

MONEY_FIELDS = {"subtotal", "cgst", "sgst", "igst", "total"}
DATE_FIELDS = {"invoice_date", "due_date"}
CODE_FIELDS = {"vendor_gstin", "buyer_gstin", "bank_ifsc", "bank_account_last4", "currency"}
REF_FIELDS = {"invoice_number", "po_number"}
SCORED_FIELDS = (
    "vendor_name", "vendor_gstin", "buyer_gstin", "invoice_number", "invoice_date", "due_date",
    "po_number", "currency", "subtotal", "cgst", "sgst", "igst", "total",
    "bank_account_last4", "bank_ifsc",
)  # fmt: skip


def _decimal(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def normalize(field: str, value: Any) -> str | None:
    """Exact match after normalisation of dates, decimals, case and whitespace."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if field in MONEY_FIELDS:
        d = _decimal(value)
        return None if d is None else str(d)
    if field in DATE_FIELDS:
        try:
            return date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            return str(value).strip()
    text = re.sub(r"\s+", " ", str(value)).strip()
    if field in CODE_FIELDS:
        return text.replace(" ", "").upper()
    if field in REF_FIELDS:
        return text.replace(" ", "").upper()
    # names: ignore case and trailing punctuation differences
    return re.sub(r"[.,]", "", text).casefold()


@dataclass
class FieldScore:
    field: str
    expected: str | None
    actual: str | None

    @property
    def correct(self) -> bool:
        return self.expected == self.actual


def score_fields(expected: dict, actual: dict | None) -> list[FieldScore]:
    actual = actual or {}
    return [
        FieldScore(f, normalize(f, expected.get(f)), normalize(f, actual.get(f)))
        for f in SCORED_FIELDS
    ]


def _line_equal(e: dict, a: dict) -> bool:
    for k in ("quantity", "unit_price", "amount"):
        if _decimal(e.get(k)) != _decimal(a.get(k)):
            return False
    return similarity(str(e.get("description", "")), str(a.get("description", ""))) >= 0.6


def line_item_counts(expected: list[dict], actual: list[dict] | None) -> tuple[int, int, int]:
    """(true positives, predicted, expected), pairing each expected line at most once."""
    actual = list(actual or [])
    used: set[int] = set()
    tp = 0
    for e in expected:
        for i, a in enumerate(actual):
            if i not in used and _line_equal(e, a):
                used.add(i)
                tp += 1
                break
    return tp, len(actual), len(expected)


def f1(tp: int, predicted: int, expected: int) -> float:
    if predicted == 0 and expected == 0:
        return 1.0
    p = tp / predicted if predicted else 0.0
    r = tp / expected if expected else 0.0
    return 2 * p * r / (p + r) if p + r else 0.0


@dataclass
class CaseResult:
    case_id: str
    category: str
    requires: list[str]
    expected_route: str
    predicted_route: str | None
    expected_match: str | None
    predicted_match: str | None
    fields: list[FieldScore] = field(default_factory=list)
    lines: tuple[int, int, int] = (0, 0, 0)
    model: str | None = None
    cost_usd: float = 0.0
    list_price_usd: float = 0.0
    latency_ms: int = 0
    attempts: int = 0
    error: str | None = None
    route_reasons: list[str] = field(default_factory=list)
    invoice_id: str | None = None  # the eval tenant's invoice row
    validation_issues: list[dict] = field(default_factory=list)

    @property
    def deferred(self) -> bool:
        """Needs a capability that is not built yet (e.g. anomaly rules, M5)."""
        return bool(self.requires)

    @property
    def false_auto_approval(self) -> bool:
        return self.predicted_route == "auto_approve" and self.expected_route != "auto_approve"

    def to_json(self) -> dict:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "requires": self.requires,
            "expected_route": self.expected_route,
            "predicted_route": self.predicted_route,
            "expected_match": self.expected_match,
            "predicted_match": self.predicted_match,
            "field_accuracy": (
                sum(f.correct for f in self.fields) / len(self.fields) if self.fields else 0.0
            ),
            "wrong_fields": [
                {"field": f.field, "expected": f.expected, "actual": f.actual}
                for f in self.fields
                if not f.correct
            ],
            "line_items": {
                "tp": self.lines[0],
                "predicted": self.lines[1],
                "expected": self.lines[2],
                "f1": round(f1(*self.lines), 4),
            },
            "model": self.model,
            "cost_usd": self.cost_usd,
            "list_price_usd": self.list_price_usd,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "error": self.error,
            "route_reasons": self.route_reasons,
            "validation_issues": [f"{i['check']}: {i['message']}" for i in self.validation_issues],
        }


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


def aggregate(results: list[CaseResult]) -> dict:
    gated = [r for r in results if not r.deferred]
    fields = [f for r in results for f in r.fields]
    tp = sum(r.lines[0] for r in results)
    pred = sum(r.lines[1] for r in results)
    exp = sum(r.lines[2] for r in results)
    by_field: dict[str, list[bool]] = {}
    for f in fields:
        by_field.setdefault(f.field, []).append(f.correct)
    by_model: dict[str, list[float]] = {}
    for r in results:
        if r.fields:
            acc = sum(f.correct for f in r.fields) / len(r.fields)
            by_model.setdefault(r.model or "none", []).append(acc)
    by_category: dict[str, list[float]] = {}
    for r in results:
        acc = sum(f.correct for f in r.fields) / len(r.fields) if r.fields else 0.0
        by_category.setdefault(r.category, []).append(acc)
    with_match = [r for r in results if r.expected_match is not None]
    latencies = [r.latency_ms for r in results if not r.error]
    models = [r.model for r in results if r.model]
    return {
        "n_cases": len(results),
        "n_gated": len(gated),
        "errors": sum(1 for r in results if r.error),
        "field_accuracy": sum(f.correct for f in fields) / len(fields) if fields else 0.0,
        "line_item_f1": f1(tp, pred, exp),
        "routing_accuracy": (
            sum(r.predicted_route == r.expected_route for r in gated) / len(gated) if gated else 0.0
        ),
        "routing_accuracy_all": (
            sum(r.predicted_route == r.expected_route for r in results) / len(results)
            if results
            else 0.0
        ),
        "false_auto_approvals": sum(r.false_auto_approval for r in gated),
        "false_auto_approvals_deferred": sum(r.false_auto_approval for r in results if r.deferred),
        "auto_approval_rate": (
            sum(r.predicted_route == "auto_approve" for r in results) / len(results)
            if results
            else 0.0
        ),
        "match_accuracy": (
            sum(r.predicted_match == r.expected_match for r in with_match) / len(with_match)
            if with_match
            else None
        ),
        "avg_cost_usd": statistics.fmean([r.cost_usd for r in results]) if results else 0.0,
        "avg_list_price_usd": (
            statistics.fmean([r.list_price_usd for r in results]) if results else 0.0
        ),
        "p50_latency_ms": percentile(latencies, 50),
        "p95_latency_ms": percentile(latencies, 95),
        "model": max(set(models), key=models.count) if models else None,
        "models": {m: models.count(m) for m in set(models)},
        "by_field": {k: sum(v) / len(v) for k, v in sorted(by_field.items())},
        "by_model": {k: statistics.fmean(v) for k, v in by_model.items()},
        "by_category": {k: statistics.fmean(v) for k, v in by_category.items()},
    }


def gate(metrics: dict, baseline: dict | None, max_drop_points: float = 2.0) -> list[str]:
    """Reasons the run fails the CI gate (empty list = pass)."""
    failures = []
    if metrics["false_auto_approvals"]:
        failures.append(f"{metrics['false_auto_approvals']} false auto-approval(s)")
    if baseline:
        floor = baseline["field_accuracy"] - max_drop_points / 100
        if metrics["field_accuracy"] < floor:
            failures.append(
                f"field accuracy {metrics['field_accuracy']:.2%} is more than "
                f"{max_drop_points:g} points below baseline {baseline['field_accuracy']:.2%}"
            )
    return failures
