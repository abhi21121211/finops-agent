"""Upload → worker → review → resume, against real Postgres (data, vendor master and the
LangGraph checkpointer). Storage, queue and LLM are fakes."""

import uuid

import pytest

from app.agents.checkpointer import postgres_checkpointer
from app.agents.graph import build_graph
from app.core.auth import create_token
from app.llm.router import LLMRouter
from app.worker import process_invoice, resume_invoice
from tests.conftest import FakeOpenAI, as_json, make_pdf, sample_extraction


@pytest.fixture
def worker_storage(storage, monkeypatch):
    monkeypatch.setattr("app.worker.get_storage", lambda: storage)
    return storage


async def _upload(client, headers, data: bytes = b"", name: str = "inv.pdf"):
    return await client.post(
        "/api/v1/invoices",
        headers=headers,
        files={"file": (name, data or make_pdf(), "application/pdf")},
    )


async def _process(invoice_id: str, *replies: str) -> None:
    """One worker 'process' with its own checkpointer, like a separate worker process."""
    async with postgres_checkpointer() as saver:
        ctx = {"graph": build_graph(saver), "job_try": 1}
        llm = LLMRouter(client=FakeOpenAI(*replies))
        await process_invoice(ctx, invoice_id, configurable={"llm": llm})


async def _resume(invoice_id: str, decision: dict) -> None:
    async with postgres_checkpointer() as saver:
        await resume_invoice({"graph": build_graph(saver), "job_try": 1}, invoice_id, decision)


async def _get(client, headers, invoice_id):
    return (await client.get(f"/api/v1/invoices/{invoice_id}", headers=headers)).json()


async def test_requires_auth(client):
    assert (await client.get("/api/v1/invoices")).status_code == 401


async def test_rejects_non_invoice_files(client, auth_headers):
    resp = await _upload(client, auth_headers, b"MZ\x90\x00 not a pdf", "evil.exe")
    assert resp.status_code == 415


async def test_clean_invoice_is_auto_approved(client, auth_headers, queue, worker_storage):
    body = (await _upload(client, auth_headers)).json()
    assert body["status"] == "received" and queue.enqueued == [body["id"]]

    await _process(body["id"], as_json(sample_extraction(invoice_number="AUTO-1")))

    detail = await _get(client, auth_headers, body["id"])
    assert detail["status"] == "approved"
    assert detail["route"] == "auto_approve"
    assert detail["validation_issues"] == []
    assert len(detail["pages"]) == 1
    nodes = [e["node"] for e in detail["events"]]
    assert nodes[:4] == ["workflow", "intake", "extract", "validate"]
    assert nodes[-2:] == ["post", "workflow"]


async def test_pause_survives_restart_then_edit_and_approve(
    client, auth_headers, queue, worker_storage
):
    inv_id = (await _upload(client, auth_headers)).json()["id"]
    reply = sample_extraction(invoice_number="PAUSE-1")
    reply["confidence"]["po_number"] = 0.5
    await _process(inv_id, as_json(reply))  # worker #1 exits after the pause

    detail = await _get(client, auth_headers, inv_id)
    assert detail["status"] == "needs_review"
    assert any("po_number" in r for r in detail["route_reasons"])

    resp = await client.post(
        f"/api/v1/invoices/{inv_id}/review",
        headers=auth_headers,
        json={"action": "edit", "field_edits": {"po_number": "PO-2026-0412"}, "comment": "ok"},
    )
    assert resp.status_code == 200 and resp.json()["status"] == "processing"
    [(resumed_id, decision)] = queue.resumed
    assert resumed_id == inv_id and decision["user_email"] == "demo@finops.local"

    # A brand-new graph + checkpointer: nothing in memory from worker #1.
    await _resume(inv_id, decision)

    detail = await _get(client, auth_headers, inv_id)
    assert detail["status"] == "approved"
    assert detail["extraction"]["po_number"] == "PO-2026-0412"
    assert detail["field_confidence"]["po_number"] == 1.0
    assert detail["reviews"][0]["action"] == "edit"
    assert detail["reviews"][0]["user_email"] == "demo@finops.local"


async def test_review_guards(client, auth_headers, queue, worker_storage):
    inv_id = (await _upload(client, auth_headers)).json()["id"]
    url = f"/api/v1/invoices/{inv_id}/review"

    # Not paused yet.
    assert (
        await client.post(url, headers=auth_headers, json={"action": "approve"})
    ).status_code == 409

    await _process(
        inv_id,
        as_json(sample_extraction(invoice_number="G-1", total=99999)),
        as_json(sample_extraction(invoice_number="G-1", total=99999)),
    )
    assert (await _get(client, auth_headers, inv_id))["status"] == "needs_review"

    bad_edits = [
        {"action": "edit", "field_edits": {}},
        {"action": "edit", "field_edits": {"not_a_field": 1}},
        {"action": "edit", "field_edits": {"invoice_date": "yesterday"}},
        {"action": "approve", "field_edits": {"po_number": "X"}},
    ]
    for body in bad_edits:
        assert (await client.post(url, headers=auth_headers, json=body)).status_code == 422, body

    assert (
        await client.post(url, headers=auth_headers, json={"action": "reject"})
    ).status_code == 200
    # Second click while the first is being processed.
    assert (
        await client.post(url, headers=auth_headers, json={"action": "approve"})
    ).status_code == 409


async def test_duplicate_upload_goes_to_review(client, auth_headers, queue, worker_storage):
    reply = as_json(sample_extraction(invoice_number="DUP-1"))
    first = (await _upload(client, auth_headers)).json()["id"]
    await _process(first, reply)
    second = (await _upload(client, auth_headers)).json()["id"]
    await _process(second, reply)

    detail = await _get(client, auth_headers, second)
    assert detail["status"] == "needs_review"
    assert [i["check"] for i in detail["validation_issues"]] == ["duplicate"]


async def test_reprocess(client, auth_headers, queue, worker_storage):
    inv_id = (await _upload(client, auth_headers)).json()["id"]
    url = f"/api/v1/invoices/{inv_id}/reprocess"
    assert (await client.post(url, headers=auth_headers)).status_code == 409  # still received

    await _process(inv_id, "garbage", "garbage")
    assert (await _get(client, auth_headers, inv_id))["status"] == "failed"

    assert (await client.post(url, headers=auth_headers)).status_code == 200
    assert queue.reprocessed == [inv_id]


async def test_unparseable_llm_output_marks_failed(client, auth_headers, worker_storage):
    inv_id = (await _upload(client, auth_headers)).json()["id"]
    await _process(inv_id, "garbage", "more garbage")
    detail = await _get(client, auth_headers, inv_id)
    assert detail["status"] == "failed"
    assert "No valid output" in detail["error_message"]


async def test_other_tenant_cannot_read_or_review_invoice(client, auth_headers):
    inv_id = (await _upload(client, auth_headers)).json()["id"]
    other = create_token(uuid.uuid4(), uuid.uuid4(), "x@other.test", "admin")
    headers = {"Authorization": f"Bearer {other}"}

    assert (await client.get(f"/api/v1/invoices/{inv_id}", headers=headers)).status_code == 404
    review = await client.post(
        f"/api/v1/invoices/{inv_id}/review", headers=headers, json={"action": "approve"}
    )
    assert review.status_code == 404
    assert (await client.get("/api/v1/invoices", headers=headers)).json()["total"] == 0
