"""validate: deterministic checks on the extraction, and the decision whether another
extraction attempt could help."""

from datetime import date

from langchain_core.runnables import RunnableConfig

from app.agents.lookups import get_lookups
from app.agents.state import InvoiceState, event
from app.agents.tools.validation import run_all
from app.schemas.extraction import ExtractedInvoice
from app.schemas.validation import Severity, ValidationIssue

MAX_EXTRACTION_ATTEMPTS = 3


async def check_extraction(
    state: InvoiceState, config: RunnableConfig, extraction: dict
) -> list[ValidationIssue]:
    lookups = get_lookups(config)
    tenant_id = state["tenant_id"]
    return run_all(
        ExtractedInvoice.model_validate(extraction),
        today=date.today(),
        others=await lookups.other_invoices(tenant_id, state["invoice_id"]),
        known_gstins=await lookups.known_vendor_gstins(tenant_id),
    )


async def validate(state: InvoiceState, config: RunnableConfig) -> InvoiceState:
    issues = await check_extraction(state, config, state["extraction"])
    errors = [i for i in issues if i.severity == Severity.error]
    fixable = [i for i in errors if i.fixable]

    # A re-read that returns exactly the same values means the document really says that;
    # more attempts would only burn tokens.
    confirmed = state.get("previous_extraction") == state["extraction"]
    retry = (
        bool(fixable) and not confirmed and state["extraction_attempts"] < MAX_EXTRACTION_ATTEMPTS
    )

    if not issues:
        detail = "all checks passed"
    else:
        detail = "; ".join(i.message for i in issues[:3]) + (" …" if len(issues) > 3 else "")
        if retry:
            detail = f"re-extracting with feedback: {detail}"
        elif fixable and confirmed:
            detail = f"re-read confirmed the document; {detail}"
    return {
        "validation_issues": [i.model_dump(mode="json") for i in issues],
        "retry_extraction": retry,
        "events": [event("validate", "completed" if not errors else "info", detail)],
    }


def after_validate(state: InvoiceState) -> str:
    return "extract" if state.get("retry_extraction") else "reconcile"
