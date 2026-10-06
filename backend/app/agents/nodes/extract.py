"""extract: multimodal LLM → ExtractedInvoice + per-field confidence."""

from langchain_core.runnables import RunnableConfig

from app.agents.prompts import load_prompt
from app.agents.state import InvoiceState, event
from app.agents.tools.validation import TOLERANCE
from app.llm.router import LLMOutputError, LLMRouter, Tier, image_part, text_part
from app.schemas.extraction import ExtractionOutput
from app.storage import get_storage

PROMPT = "extract_v2"
MAX_TEXT_CHARS = 12_000


def _router(config: RunnableConfig | None) -> LLMRouter:
    injected = (config or {}).get("configurable", {}).get("llm")
    return injected or LLMRouter()


async def extract(state: InvoiceState, config: RunnableConfig) -> InvoiceState:
    storage = get_storage()
    attempts = state.get("extraction_attempts", 0) + 1

    content = [text_part(f"Invoice with {len(state['page_keys'])} page image(s) follows.")]
    for key in state["page_keys"]:
        content.append(image_part(await storage.get(key)))
    if state.get("pdf_text"):
        content.append(
            text_part(
                "Text layer extracted from the PDF (more reliable than the image for exact "
                "characters; use the images for layout):\n<document_text>\n"
                f"{state['pdf_text'][:MAX_TEXT_CHARS]}\n</document_text>"
            )
        )

    feedback = [i for i in state.get("validation_issues") or [] if i.get("fixable")]
    if feedback and state.get("extraction"):
        problems = "\n".join(f"- {i['field'] or i['check']}: {i['message']}" for i in feedback)
        content.append(
            text_part(
                "A checker found these problems in your previous answer (tolerance "
                f"₹{TOLERANCE}). Re-read the document for these fields:\n{problems}"
            )
        )

    try:
        result = await _router(config).structured(
            tier=Tier.vision,
            system=load_prompt(PROMPT),
            user_content=content,
            schema=ExtractionOutput,
        )
    except LLMOutputError as e:
        if state.get("extraction"):
            # A retry failed: keep the earlier extraction and mark it final so validation
            # routes it instead of retrying again.
            return {
                "extraction_attempts": attempts,
                "previous_extraction": state["extraction"],
                "events": [event("extract", "info", "retry produced invalid output; kept earlier")],
            }
        return {
            "extraction_attempts": attempts,
            "error": str(e),
            "model_used": e.calls[-1].model if e.calls else "",
            "cost_usd": float(sum(c.cost_usd for c in e.calls)),
            "list_price_usd": float(sum(c.list_price_usd for c in e.calls)),
            "llm_latency_ms": sum(c.latency_ms for c in e.calls),
            "events": [event("extract", "failed", "model output never matched the schema")],
        }

    out = result.output
    low = sorted(k for k, v in out.confidence.items() if v < 0.85)
    detail = f"{len(out.invoice.line_items)} line item(s) via {result.model}"
    if low:
        detail += f"; low confidence: {', '.join(low)}"
    if attempts > 1:
        detail = f"attempt {attempts}: {detail}"
    return {
        "previous_extraction": state.get("extraction"),
        "extraction": out.invoice.model_dump(mode="json"),
        "field_confidence": out.confidence,
        "extraction_attempts": attempts,
        "model_used": result.model,
        "cost_usd": float(result.cost_usd),
        "list_price_usd": float(result.list_price_usd),
        "llm_latency_ms": result.latency_ms,
        "error": None,
        "events": [event("extract", "completed", detail)],
    }
