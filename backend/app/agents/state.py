"""Workflow state (spec §4). Fields for later milestones are declared now so the shape
stays stable; nodes fill only what they own."""

import operator
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, TypedDict


class WorkflowEvent(TypedDict):
    at: str
    node: str
    status: Literal["started", "completed", "failed", "info"]
    detail: str


def event(node: str, status: str, detail: str = "") -> WorkflowEvent:
    return WorkflowEvent(at=datetime.now(UTC).isoformat(), node=node, status=status, detail=detail)


class InvoiceState(TypedDict, total=False):
    invoice_id: str
    tenant_id: str
    file_keys: list[str]  # original uploads
    content_types: list[str]
    source: Literal["upload", "email", "cli"]

    page_keys: list[str]  # rendered JPEG pages (set by intake)
    page_hashes: list[str]
    pdf_text: str

    extraction: dict[str, Any] | None  # ExtractedInvoice as JSON
    previous_extraction: dict[str, Any] | None  # from the attempt before, to spot no-change
    retry_extraction: bool
    field_confidence: dict[str, float]
    validation_issues: list[dict[str, Any]]
    extraction_attempts: int
    match_result: dict[str, Any] | None
    anomalies: list[dict[str, Any]]
    route: Literal["auto_approve", "human_review", "vendor_query", "reject"] | None
    route_reasons: list[str]
    human_decision: dict[str, Any] | None
    outcome: Literal["approved", "rejected"] | None

    model_used: str
    cost_usd: Annotated[float, operator.add]
    list_price_usd: Annotated[float, operator.add]
    llm_latency_ms: Annotated[int, operator.add]
    events: Annotated[list[WorkflowEvent], operator.add]
    error: str | None
