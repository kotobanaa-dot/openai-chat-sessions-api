"""Spending report. HTTP layer only: gather the aggregates, serialise them."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import StatsRepoDep, require_api_key
from app.schemas.stats import DayOut, FailedOut, ModelStatOut, OverallOut, StatsOut

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_api_key)])


@router.get("/stats", response_model=StatsOut)
async def get_stats(
    repo: StatsRepoDep,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
) -> StatsOut:
    """Where the money went: totals, a breakdown per model, and the daily curve.

    Archived generations are included on purpose. A reset clears the active
    context, not the invoice, and a spending report that quietly dropped
    archived turns would understate what was actually paid.
    """
    return StatsOut(
        overall=OverallOut.of(await repo.overall()),
        by_model=[ModelStatOut.of(row) for row in await repo.by_model()],
        by_day=[DayOut.of(row) for row in await repo.by_day(limit=days)],
        failed=FailedOut.of(await repo.failed()),
    )
