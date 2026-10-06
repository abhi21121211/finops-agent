"""intake: render uploads into page images + text. Injection scan and duplicate checks
arrive with the guardrail and anomaly milestones."""

import asyncio
import hashlib

from app.agents.state import InvoiceState, event
from app.agents.tools.documents import render
from app.core.config import get_settings
from app.storage import get_storage, tenant_key


async def intake(state: InvoiceState) -> InvoiceState:
    storage = get_storage()
    max_pages = get_settings().max_pages
    page_keys: list[str] = []
    page_hashes: list[str] = []
    texts: list[str] = []

    for key, ctype in zip(state["file_keys"], state["content_types"], strict=True):
        data = await storage.get(key)
        doc = await asyncio.to_thread(render, data, ctype, max_pages - len(page_keys))
        for jpeg in doc.pages:
            pkey = tenant_key(
                state["tenant_id"], "invoices", state["invoice_id"], f"page-{len(page_keys)}.jpg"
            )
            await storage.put(pkey, jpeg, "image/jpeg")
            page_keys.append(pkey)
            page_hashes.append(hashlib.sha256(jpeg).hexdigest())
        if doc.text:
            texts.append(doc.text)
        if len(page_keys) >= max_pages:
            break

    detail = f"{len(page_keys)} page(s), text layer {'found' if texts else 'not found'}"
    return {
        "page_keys": page_keys,
        "page_hashes": page_hashes,
        "pdf_text": "\n\n".join(texts),
        "events": [event("intake", "completed", detail)],
    }
