"""Workflow behaviour: retry loop, routing and human review, with a fake LLM and an
in-memory checkpointer. Durability across restarts is tested in test_invoices_flow."""

import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.graph import build_graph
from app.agents.tools.validation import ExistingInvoice
from app.demo_data import KAVERI, ZEPHYR
from app.llm.router import LLMRouter
from tests.conftest import FakeLookups, FakeOpenAI, as_json, make_pdf, sample_extraction


@pytest.fixture
def run(storage):
    """Runs one invoice through a fresh graph; returns (result, snapshot, fake_llm)."""

    async def _run(*replies, lookups=None, resume=None):
        graph = build_graph(InMemorySaver())
        invoice_id = str(uuid.uuid4())
        await storage.put(f"t/{invoice_id}/original.pdf", make_pdf(), "application/pdf")
        fake = FakeOpenAI(*replies)
        config = {
            "configurable": {
                "thread_id": invoice_id,
                "llm": LLMRouter(client=fake),
                "lookups": lookups or FakeLookups(),
            }
        }
        state = {
            "invoice_id": invoice_id,
            "tenant_id": str(uuid.uuid4()),
            "file_keys": [f"t/{invoice_id}/original.pdf"],
            "content_types": ["application/pdf"],
            "source": "upload",
            "extraction_attempts": 0,
            "events": [],
        }
        result = await graph.ainvoke(state, config)
        if resume is not None:
            result = await graph.ainvoke(Command(resume=resume), config)
        return result, await graph.aget_state(config), fake

    return _run


def good(**overrides) -> str:
    return as_json(sample_extraction(**overrides))


async def test_clean_invoice_auto_approved_in_one_call(run):
    result, snap, fake = await run(good())
    assert result["route"] == "auto_approve"
    assert result["outcome"] == "approved"
    assert result["route_reasons"] == []
    assert snap.next == ()
    assert len(fake.requests) == 1


async def test_misread_fixed_on_retry_with_feedback(run):
    result, _, fake = await run(good(total=1810), good())
    assert len(fake.requests) == 2
    retry_text = str(fake.requests[1]["messages"][1]["content"])
    assert "previous answer" in retry_text and "1810" in retry_text
    assert result["extraction_attempts"] == 2
    assert result["outcome"] == "approved"


async def test_document_really_wrong_stops_when_reread_agrees(run):
    # Same values twice: the document itself is wrong. No third attempt.
    result, snap, fake = await run(good(total=1330), good(total=1330))
    assert len(fake.requests) == 2
    assert snap.next == ("human_review",)
    assert any("unresolved validation" in r for r in result["route_reasons"])
    assert any("re-read confirmed" in e["detail"] for e in result["events"])


async def test_retries_capped_at_three_attempts(run):
    result, snap, fake = await run(good(total=1330), good(total=1340), good(total=1350))
    assert len(fake.requests) == 3
    assert result["extraction_attempts"] == 3
    assert snap.next == ("human_review",)


async def test_unknown_vendor_is_not_retried(run):
    result, snap, fake = await run(good(vendor_name=ZEPHYR.name, vendor_gstin=ZEPHYR.gstin))
    assert len(fake.requests) == 1
    assert snap.next == ("human_review",)
    checks = {i["check"] for i in result["validation_issues"]}
    assert "vendor_known" in checks  # (plus a place-of-supply warning: Delhi → Maharashtra)


async def test_duplicate_goes_to_review(run):
    lookups = FakeLookups(others=[ExistingInvoice("old-id", KAVERI.gstin, "KAV/26-27/001")])
    result, snap, fake = await run(good(), lookups=lookups)
    assert len(fake.requests) == 1
    assert snap.next == ("human_review",)


async def test_low_confidence_goes_to_review(run):
    reply = sample_extraction()
    reply["confidence"]["vendor_gstin"] = 0.6
    result, snap, _ = await run(as_json(reply))
    assert snap.next == ("human_review",)
    assert "vendor_gstin (60%)" in result["route_reasons"][0]


async def test_over_limit_waits_then_approve_resumes(run):
    lookups = FakeLookups(limit="1000")
    result, snap, _ = await run(
        good(), lookups=lookups, resume={"action": "approve", "user_email": "r@x.test"}
    )
    assert result["outcome"] == "approved"
    assert result["human_decision"]["action"] == "approve"
    assert snap.next == ()
    assert result["events"][-1]["detail"] == "approved by r@x.test"


async def test_edit_applies_fields_and_marks_them_certain(run):
    reply = sample_extraction()
    reply["confidence"]["po_number"] = 0.4
    decision = {"action": "edit", "field_edits": {"po_number": "PO-2026-0412"}}
    result, _, _ = await run(as_json(reply), resume=decision)
    assert result["extraction"]["po_number"] == "PO-2026-0412"
    assert result["field_confidence"]["po_number"] == 1.0
    assert result["outcome"] == "approved"


async def test_reject(run):
    result, _, _ = await run(good(total=1330), good(total=1330), resume={"action": "reject"})
    assert result["outcome"] == "rejected"


async def test_unparseable_output_ends_without_extraction(run):
    result, snap, _ = await run("nope", "still nope")
    assert result.get("extraction") is None
    assert snap.next == ()
    assert "outcome" not in result


async def test_retry_with_bad_output_keeps_first_extraction(run):
    result, snap, fake = await run(good(total=1330), "garbage", "garbage")
    assert result["extraction"]["total"] == "1330"
    assert snap.next == ("human_review",)
    assert len(fake.requests) == 3  # 1 + one call that failed twice (correction round)
