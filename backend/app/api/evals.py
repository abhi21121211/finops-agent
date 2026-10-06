"""Evaluation history (spec §6: GET /evals/runs, /evals/runs/{id})."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUserDep
from app.db.models import EvalRun
from app.db.session import get_session

router = APIRouter(prefix="/evals", tags=["evals"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class EvalRunSummary(BaseModel):
    id: uuid.UUID
    created_at: datetime
    mode: str
    label: str | None
    git_sha: str | None
    model: str | None
    n_cases: int
    field_accuracy: float
    line_item_f1: float
    routing_accuracy: float
    false_auto_approvals: int
    auto_approval_rate: float
    match_accuracy: float | None
    avg_cost_usd: float
    p50_latency_ms: int
    p95_latency_ms: int


class EvalRunDetail(EvalRunSummary):
    report: dict[str, Any]


def _summary(r: EvalRun) -> dict:
    return {k: getattr(r, k) for k in EvalRunSummary.model_fields}


@router.get("/runs", response_model=list[EvalRunSummary])
async def list_runs(
    _: CurrentUserDep,
    session: SessionDep,
    mode: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict]:
    q = select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit)
    if mode:
        q = q.where(EvalRun.mode == mode)
    return [_summary(r) for r in await session.scalars(q)]


@router.get("/runs/{run_id}", response_model=EvalRunDetail)
async def get_run(run_id: uuid.UUID, _: CurrentUserDep, session: SessionDep) -> dict:
    run = await session.get(EvalRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Eval run not found")
    return {**_summary(run), "report": run.report}
