"""Evaluation harness (spec §8).

Runs dataset cases through the real workflow (intake → extract → validate → reconcile →
route) in a fresh, isolated tenant, scores them against ground truth, stores an eval_runs
row plus eval_results rows, writes a JSON report and applies the CI gate.

    uv run python ../evals/run.py --smoke            # 20 fixed cases (PR gate)
    uv run python ../evals/run.py                    # all 100
    uv run python ../evals/run.py --oracle           # perfect extraction: tests the rules
    uv run python ../evals/run.py --cases case-001,case-007 --no-gate

Exit code 1 when the gate fails: any false auto-approval (outside cases that need features
not built yet), or field accuracy more than 2 points below evals/baseline.json.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.agents.graph import build_graph  # noqa: E402
from app.agents.nodes import extract as extract_node  # noqa: E402
from app.agents.nodes import intake as intake_node  # noqa: E402
from app.agents.tools.documents import sniff_content_type  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.models import (  # noqa: E402
    BankTransaction,
    EvalResult,
    EvalRun,
    Invoice,
    InvoiceFile,
    InvoiceSource,
    InvoiceStatus,
    Match,
    POLine,
    PurchaseOrder,
    Tenant,
    Vendor,
)
from app.db.session import SessionLocal, engine  # noqa: E402
from app.evals.scoring import (  # noqa: E402
    CaseResult,
    aggregate,
    gate,
    line_item_counts,
    score_fields,
)
from app.llm.router import LLMRouter  # noqa: E402
from app.reconcile.bank_csv import parse_statement  # noqa: E402
from app.reconcile.service import refresh_payments, save_match  # noqa: E402
from app.schemas.extraction import CONFIDENCE_FIELDS  # noqa: E402
from app.worker import _is_transient as is_transient  # noqa: E402

DATASET = ROOT / "evals" / "dataset"
REPORTS = ROOT / "evals" / "reports"
BASELINE = ROOT / "evals" / "baseline.json"


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def ensure_bucket(self) -> None: ...

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def get(self, key: str) -> bytes:
        return self.objects[key]

    def presigned_url(self, key: str, expires_s: int = 900) -> str:
        return f"memory://{key}"


class OracleClient:
    """Stands in for the LLM and returns the ground truth: isolates rule/matching errors
    from extraction errors."""

    def __init__(self, truth: dict) -> None:
        self.truth = truth
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=self._create))
        )

    async def _create(self, **kw):
        if kw["model"] == "vision":
            body = {"invoice": self.truth, "confidence": dict.fromkeys(CONFIDENCE_FIELDS, 0.99)}
        else:
            body = {"same": [True] * 50}
        resp = SimpleNamespace(
            model="oracle",
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))],
        )
        return SimpleNamespace(headers={"x-litellm-model-name": "oracle"}, parse=lambda: resp)


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return None


async def seed_tenant() -> uuid.UUID:
    """A fresh tenant per run, so runs never see each other's invoices or payments."""
    tid = uuid.uuid4()
    vendors = json.loads((DATASET / "vendors.json").read_text())
    pos = json.loads((DATASET / "purchase_orders.json").read_text())
    async with SessionLocal() as s:
        s.add(Tenant(id=tid, name=f"eval {datetime.now(UTC):%Y-%m-%d %H:%M}"))
        await s.flush()
        ids = {}
        for v in vendors:
            row = Vendor(tenant_id=tid, **v)
            s.add(row)
            await s.flush()
            ids[v["gstin"]] = row.id
        for po in pos:
            lines = [
                POLine(
                    position=n,
                    description=ln["description"],
                    quantity=Decimal(ln["quantity"]),
                    unit_price=Decimal(ln["unit_price"]),
                )
                for n, ln in enumerate(po["lines"])
            ]
            s.add(
                PurchaseOrder(
                    tenant_id=tid,
                    vendor_id=ids[po["vendor_gstin"]],
                    po_number=po["po_number"],
                    date=date.fromisoformat(po["date"]),
                    lines=lines,
                    total=sum((ln.quantity * ln.unit_price for ln in lines), Decimal(0)),
                )
            )
        for t in parse_statement((DATASET / "bank_statement.csv").read_bytes()):
            s.add(
                BankTransaction(
                    tenant_id=tid,
                    date=t.date,
                    amount=t.amount,
                    narration=t.narration,
                    reference=t.reference,
                    fingerprint=t.fingerprint,
                )
            )
        await s.commit()
    return tid


async def run_case(
    case: dict, tid: uuid.UUID, graph, storage: MemoryStorage, oracle: bool, max_attempts: int = 4
) -> CaseResult:
    data = (DATASET / "cases" / case["id"] / case["file"]).read_bytes()
    ctype = sniff_content_type(data)
    invoice_id = uuid.uuid4()
    key = f"tenants/{tid}/invoices/{invoice_id}/original"
    await storage.put(key, data, ctype)
    async with SessionLocal() as s:
        s.add(
            Invoice(
                id=invoice_id,
                tenant_id=tid,
                status=InvoiceStatus.processing,
                source=InvoiceSource.cli,
                original_filename=f"{case['id']}/{case['file']}",
                events=[],
                files=[InvoiceFile(s3_key=key, content_type=ctype, page_no=0, sha256="eval")],
            )
        )
        await s.commit()

    configurable = {"thread_id": str(invoice_id)}
    if oracle:
        configurable["llm"] = LLMRouter(client=OracleClient(case["expected_extraction"]))
    config = {"configurable": configurable}
    state = {
        "invoice_id": str(invoice_id),
        "tenant_id": str(tid),
        "file_keys": [key],
        "content_types": [ctype],
        "source": "cli",
        "extraction_attempts": 0,
        "events": [],
    }

    started, error, attempts, values = time.perf_counter(), None, 0, {}
    graph_input = state
    while attempts < max_attempts:
        attempts += 1
        try:
            values = await graph.ainvoke(graph_input, config)
            break
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)[:300]}"
            if not is_transient(e):
                break  # a crash is a result, not a harness failure
            graph_input = None  # resume from the checkpoint
            await asyncio.sleep(20 * attempts)
    if values.get("route"):
        error = None  # reached a routing decision, possibly after transient retries
    elif values.get("error"):
        error = values["error"][:300]  # e.g. model output never matched the schema
    latency = int((time.perf_counter() - started) * 1000)

    async with SessionLocal() as s:
        inv = await s.get(Invoice, invoice_id)
        inv.extraction = values.get("extraction")
        if inv.extraction:
            inv.total = Decimal(str(inv.extraction["total"]))
            inv.invoice_date = date.fromisoformat(inv.extraction["invoice_date"])
        inv.route = values.get("route")
        inv.status = (
            InvoiceStatus.approved
            if values.get("outcome") == "approved"
            else InvoiceStatus.needs_review
            if values.get("route") == "human_review"
            else InvoiceStatus.failed
        )
        if values.get("match_result"):
            await save_match(s, tid, invoice_id, values["match_result"])
        await s.commit()

    return CaseResult(
        case_id=case["id"],
        category=case["category"],
        requires=case["requires"],
        expected_route=case["expected_route"],
        predicted_route=values.get("route"),
        expected_match=case["expected_match"],
        predicted_match=None,
        fields=score_fields(case["expected_extraction"], values.get("extraction")),
        lines=line_item_counts(
            case["expected_extraction"]["line_items"],
            (values.get("extraction") or {}).get("line_items"),
        ),
        model=values.get("model_used") or None,
        cost_usd=float(values.get("cost_usd", 0)),
        list_price_usd=float(values.get("list_price_usd", 0)),
        latency_ms=latency,
        attempts=attempts,
        error=error,
        route_reasons=values.get("route_reasons") or [],
        invoice_id=str(invoice_id),
        validation_issues=values.get("validation_issues") or [],
    )


async def run(args) -> int:
    configure_logging("WARNING")
    manifest = json.loads((DATASET / "manifest.json").read_text())
    ids = (
        args.cases.split(",")
        if args.cases
        else manifest["smoke"]
        if args.smoke
        else manifest["cases"]
    )
    cases = [json.loads((DATASET / "cases" / i / "case.json").read_text()) for i in ids]
    mode = (
        "oracle" if args.oracle else ("custom" if args.cases else "smoke" if args.smoke else "full")
    )

    storage = MemoryStorage()
    intake_node.get_storage = extract_node.get_storage = lambda: storage
    graph = build_graph(InMemorySaver())
    tid = await seed_tenant()
    print(f"eval {mode}: {len(cases)} cases, tenant {tid}, concurrency {args.concurrency}")

    sem = asyncio.Semaphore(args.concurrency)
    done = 0

    async def one(case: dict) -> CaseResult:
        nonlocal done
        async with sem:
            r = await run_case(case, tid, graph, storage, args.oracle)
        done += 1
        mark = "✓" if r.predicted_route == r.expected_route and not r.error else "✗"
        acc = sum(f.correct for f in r.fields) / len(r.fields)
        print(
            f"  [{done:3d}/{len(cases)}] {mark} {r.case_id} {r.category:9} "
            f"fields {acc:5.0%} route {r.predicted_route}/{r.expected_route} "
            f"{(r.model or '')[:32]} {r.latency_ms / 1000:.1f}s"
            + (f"  ERROR {r.error[:80]}" if r.error else ""),
            flush=True,
        )
        return r

    # Cases that depend on another (duplicates) run after everything else.
    first = [c for c in cases if not c.get("depends_on")]
    later = [c for c in cases if c.get("depends_on")]
    results = list(await asyncio.gather(*(one(c) for c in first)))
    results += list(await asyncio.gather(*(one(c) for c in later)))

    async with SessionLocal() as s:
        await refresh_payments(s, tid)
        rows = await s.execute(select(Match.invoice_id, Match.status).where(Match.tenant_id == tid))
        statuses = {str(iid): status.value for iid, status in rows}
    in_run = {c["id"] for c in cases}
    for r, case in zip(results, first + later, strict=True):
        r.predicted_match = statuses.get(r.invoice_id)
        # A combined transfer only matches if every invoice it pays is in this run.
        if not set(case.get("payment_group") or []) <= in_run:
            r.expected_match = None

    metrics = aggregate(results)
    baseline_all = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    baseline = baseline_all.get(mode)
    failures = gate(metrics, baseline) if not args.no_gate else []

    report = {
        "mode": mode,
        "label": args.label,
        "git_sha": git_sha(),
        "created_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
        "baseline": baseline,
        "gate_failures": failures,
        "cases": [r.to_json() for r in results],
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{mode}.json"
    path.write_text(json.dumps(report, indent=2, default=str))

    if args.write_baseline:
        write_baseline(mode, metrics, results, manifest, report["git_sha"])

    if not args.no_record:
        async with SessionLocal() as s:
            run_row = EvalRun(
                git_sha=report["git_sha"],
                label=args.label,
                mode=mode,
                model=metrics["model"],
                n_cases=metrics["n_cases"],
                field_accuracy=metrics["field_accuracy"],
                line_item_f1=metrics["line_item_f1"],
                routing_accuracy=metrics["routing_accuracy"],
                false_auto_approvals=metrics["false_auto_approvals"],
                auto_approval_rate=metrics["auto_approval_rate"],
                match_accuracy=metrics["match_accuracy"],
                avg_cost_usd=metrics["avg_list_price_usd"],
                p50_latency_ms=metrics["p50_latency_ms"],
                p95_latency_ms=metrics["p95_latency_ms"],
                report=report,
            )
            s.add(run_row)
            await s.flush()
            for r in results:
                for f in r.fields:
                    s.add(
                        EvalResult(
                            eval_run_id=run_row.id,
                            case_id=r.case_id,
                            field=f.field,
                            expected=f.expected,
                            actual=f.actual,
                            correct=f.correct,
                        )
                    )
                s.add(
                    EvalResult(
                        eval_run_id=run_row.id,
                        case_id=r.case_id,
                        field="route",
                        expected=r.expected_route,
                        actual=r.predicted_route,
                        correct=r.predicted_route == r.expected_route,
                    )
                )
            await s.commit()
            print(f"recorded eval run {run_row.id}")
    await engine.dispose()

    m = metrics
    print("\n── results ─────────────────────────────────────────")
    print(
        f"field accuracy        {m['field_accuracy']:.2%}"
        + (f"   (baseline {baseline['field_accuracy']:.2%})" if baseline else "")
    )
    print(f"line-item F1          {m['line_item_f1']:.2%}")
    print(
        f"routing accuracy      {m['routing_accuracy']:.2%} on {m['n_gated']} gated cases"
        f" ({m['routing_accuracy_all']:.2%} incl. deferred)"
    )
    print(
        f"false auto-approvals  {m['false_auto_approvals']}"
        f"   (+{m['false_auto_approvals_deferred']} in cases needing M5 anomaly rules)"
    )
    print(f"auto-approval rate    {m['auto_approval_rate']:.2%}")
    if m["match_accuracy"] is not None:
        print(f"reconciliation acc.   {m['match_accuracy']:.2%}")
    print(
        f"cost / invoice        ${m['avg_cost_usd']:.4f} actual, "
        f"${m['avg_list_price_usd']:.4f} at list price"
    )
    print(
        f"latency p50 / p95     {m['p50_latency_ms'] / 1000:.1f}s / "
        f"{m['p95_latency_ms'] / 1000:.1f}s"
    )
    print(f"errors                {m['errors']}   models {m['models']}")
    print(f"report                {path.relative_to(ROOT)}")
    if failures:
        print("\nGATE FAILED: " + "; ".join(failures))
        return 1
    print("\ngate passed" if not args.no_gate else "\ngate skipped")
    return 0


def write_baseline(
    mode: str, metrics: dict, results: list[CaseResult], manifest: dict, sha: str | None
) -> None:
    """Record this run as the gate's reference. A full run also sets the smoke baseline from
    the same results restricted to the smoke cases, so both come from one measurement."""
    data = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    stamp = {"git_sha": sha, "created_at": datetime.now(UTC).isoformat()}
    keys = ("field_accuracy", "line_item_f1", "routing_accuracy", "false_auto_approvals")
    data[mode] = {k: metrics[k] for k in keys} | stamp
    if mode == "full":
        smoke = aggregate([r for r in results if r.case_id in set(manifest["smoke"])])
        data["smoke"] = {k: smoke[k] for k in keys} | stamp | {"derived_from": "full"}
    BASELINE.write_text(json.dumps(data, indent=2) + "\n")
    print(f"baseline updated: {', '.join(k for k in data if k in (mode, 'smoke'))}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--smoke", action="store_true", help="the fixed 20-case subset")
    p.add_argument("--cases", help="comma-separated case ids")
    p.add_argument("--oracle", action="store_true", help="ground truth instead of the LLM")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--label", help="free-text label stored with the run")
    p.add_argument("--no-record", action="store_true", help="don't write eval_runs rows")
    p.add_argument("--no-gate", action="store_true")
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="store this run as the gate baseline (a full run also sets smoke)",
    )
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
