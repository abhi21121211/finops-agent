"""Reconciliation through the real workflow and API: PO match, bank upload, refresh."""

from tests.conftest import as_json, sample_extraction
from tests.test_invoices_flow import _get, _process, _upload


def statement(*rows: tuple[str, str, str]) -> bytes:
    body = "".join(f'{d},{n},"{a}",\n' for d, n, a in rows)
    return f"Date,Narration,Withdrawal Amt,Deposit Amt\n{body}".encode()


async def _upload_statement(client, headers, data: bytes):
    return await client.post(
        "/api/v1/bank-statements", headers=headers, files={"file": ("s.csv", data, "text/csv")}
    )


async def _refresh():
    from app.db.session import SessionLocal
    from app.reconcile.service import refresh_payments
    from app.seed import DEMO_TENANT_ID

    async with SessionLocal() as s:
        await refresh_payments(s, DEMO_TENANT_ID)


async def test_po_match_then_payment_with_tds(client, auth_headers, queue, storage, monkeypatch):
    monkeypatch.setattr("app.worker.get_storage", lambda: storage)
    inv_id = (await _upload(client, auth_headers)).json()["id"]
    await _process(inv_id, as_json(sample_extraction(invoice_number="REC/26-27/0001")))

    detail = await _get(client, auth_headers, inv_id)
    assert detail["status"] == "approved"
    assert detail["match"]["status"] == "unpaid"
    assert detail["match"]["po"]["po_number"] == "PO-1"

    # 2% TDS on taxable 1,000 → 1,160 paid against 1,180.
    data = statement(("01/10/2026", "NEFT KAVERI OFFICE REC-26-27-0001", "1,160.00"))
    resp = await _upload_statement(client, auth_headers, data)
    assert resp.json() == {"rows": 1, "inserted": 1, "duplicates": 0, "money_out": 1}
    assert queue.reconciled
    again = await _upload_statement(client, auth_headers, data)
    assert again.json()["duplicates"] == 1  # re-upload is idempotent

    await _refresh()
    match = (await _get(client, auth_headers, inv_id))["match"]
    assert match["status"] == "matched"
    assert match["payment"]["tds_rate"] == "2"

    rows = (await client.get("/api/v1/reconciliation", headers=auth_headers)).json()
    row = next(r for r in rows if r["invoice_id"] == inv_id)
    assert row["status"] == "matched" and row["outstanding"] == "0"


async def test_po_price_mismatch_goes_to_review(client, auth_headers, storage, monkeypatch):
    monkeypatch.setattr("app.worker.get_storage", lambda: storage)
    inv_id = (await _upload(client, auth_headers)).json()["id"]
    line = {
        "description": "Widget",
        "quantity": 2,
        "unit_price": 560,
        "tax_rate": 18,
        "amount": 1120,
    }
    reply = sample_extraction(
        invoice_number="REC/26-27/0002",
        line_items=[line],
        subtotal=1120,
        cgst="100.80",
        sgst="100.80",
        total="1321.60",
    )
    await _process(inv_id, as_json(reply))
    detail = await _get(client, auth_headers, inv_id)
    assert detail["status"] == "needs_review"
    assert detail["match"]["status"] == "mismatch"
    assert any("PO mismatch" in r and "+12.0%" in r for r in detail["route_reasons"])


async def test_statement_before_invoice_matches_on_arrival(
    client, auth_headers, storage, monkeypatch
):
    monkeypatch.setattr("app.worker.get_storage", lambda: storage)
    number = "REC/26-27/0003"
    data = statement(("02/10/2026", f"NEFT KAVERI {number}", "1180.00"))
    await _upload_statement(client, auth_headers, data)

    first = (await _upload(client, auth_headers)).json()["id"]
    await _process(first, as_json(sample_extraction(invoice_number=number, total=1180)))
    assert (await _get(client, auth_headers, first))["match"]["status"] == "matched"


async def test_master_data_endpoints(client, auth_headers):
    vendors = (await client.get("/api/v1/vendors", headers=auth_headers)).json()
    assert {"Kaveri Office Supplies LLP", "Nilgiri Cloud Services Pvt Ltd"} <= {
        v["name"] for v in vendors
    }
    bad = await client.post(
        "/api/v1/vendors", headers=auth_headers, json={"name": "X", "gstin": "27KAVCO4821K1ZK"}
    )
    assert bad.status_code == 422

    kav = next(v for v in vendors if v["name"].startswith("Kaveri"))
    po = await client.post(
        "/api/v1/purchase-orders",
        headers=auth_headers,
        json={
            "vendor_id": kav["id"],
            "po_number": "PO-TEST-1",
            "date": "2026-10-01",
            "lines": [{"description": "Chairs", "quantity": 4, "unit_price": 2500}],
        },
    )
    assert po.status_code == 201 and po.json()["total"] == "10000.00"
    dup = await client.post(
        "/api/v1/purchase-orders",
        headers=auth_headers,
        json={
            "vendor_id": kav["id"],
            "po_number": "PO-TEST-1",
            "date": "2026-10-01",
            "lines": [{"description": "Chairs", "quantity": 1, "unit_price": 1}],
        },
    )
    assert dup.status_code == 409

    listed = (await client.get("/api/v1/purchase-orders", headers=auth_headers)).json()
    seeded = next(p for p in listed if p["po_number"] == "PO-2026-0398")
    assert seeded["lines"][0]["quantity"] == "1000.000"

    bad_csv = await client.post(
        "/api/v1/bank-statements",
        headers=auth_headers,
        files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")},
    )
    assert bad_csv.status_code == 422 and "header" in bad_csv.json()["detail"]
