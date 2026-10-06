"""Upload → queue → worker (fake LLM) → API detail. Real Postgres, fake storage/LLM."""

import uuid

from app.core.auth import create_token
from app.llm.router import LLMRouter
from app.worker import process_invoice
from tests.conftest import FakeOpenAI, as_json, make_pdf, sample_extraction


async def _upload(client, headers, data: bytes, name: str = "inv.pdf"):
    return await client.post(
        "/api/v1/invoices", headers=headers, files={"file": (name, data, "application/pdf")}
    )


async def test_requires_auth(client):
    assert (await client.get("/api/v1/invoices")).status_code == 401


async def test_rejects_non_invoice_files(client, auth_headers):
    resp = await _upload(client, auth_headers, b"MZ\x90\x00 not a pdf", "evil.exe")
    assert resp.status_code == 415


async def test_upload_process_and_read_back(client, auth_headers, storage, queue, monkeypatch):
    monkeypatch.setattr("app.worker.get_storage", lambda: storage)

    resp = await _upload(client, auth_headers, make_pdf())
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "received"
    assert queue.enqueued == [body["id"]]
    assert any(k.endswith("original.pdf") for k in storage.objects)

    llm = LLMRouter(client=FakeOpenAI(as_json(sample_extraction())))
    await process_invoice({"job_try": 1}, body["id"], config={"configurable": {"llm": llm}})

    detail = (await client.get(f"/api/v1/invoices/{body['id']}", headers=auth_headers)).json()
    assert detail["status"] == "needs_review"
    assert detail["vendor_name"] == "Test Vendor LLP"
    assert detail["total"] == "1180.00"
    assert detail["model_used"] == "fake/vision-model"
    assert len(detail["pages"]) == 1
    assert detail["pages"][0]["url"].startswith("http://storage.test/tenants/")
    assert [e["node"] for e in detail["events"]] == ["workflow", "intake", "extract", "workflow"]
    assert detail["min_confidence"] == 0.5  # unscored fields default to unsure

    listing = (await client.get("/api/v1/invoices?vendor=test vendor", headers=auth_headers)).json()
    assert body["id"] in [i["id"] for i in listing["items"]]


async def test_unparseable_llm_output_marks_failed(client, auth_headers, storage, monkeypatch):
    monkeypatch.setattr("app.worker.get_storage", lambda: storage)
    inv_id = (await _upload(client, auth_headers, make_pdf())).json()["id"]

    llm = LLMRouter(client=FakeOpenAI("garbage", "more garbage"))
    await process_invoice({"job_try": 1}, inv_id, config={"configurable": {"llm": llm}})

    detail = (await client.get(f"/api/v1/invoices/{inv_id}", headers=auth_headers)).json()
    assert detail["status"] == "failed"
    assert "No valid output" in detail["error_message"]


async def test_other_tenant_cannot_read_invoice(client, auth_headers):
    inv_id = (await _upload(client, auth_headers, make_pdf())).json()["id"]
    other = create_token(uuid.uuid4(), uuid.uuid4(), "x@other.test", "admin")
    headers = {"Authorization": f"Bearer {other}"}

    assert (await client.get(f"/api/v1/invoices/{inv_id}", headers=headers)).status_code == 404
    assert (await client.get("/api/v1/invoices", headers=headers)).json()["total"] == 0
