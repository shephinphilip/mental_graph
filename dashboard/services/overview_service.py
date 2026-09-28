"""Executive overview for one school."""

from __future__ import annotations

from dashboard.services.compute import overview_payload
from dashboard.services.data import get_prepared


async def overview(db, actor, scope):
    prepared = await get_prepared(db, actor, scope)
    return overview_payload(actor, prepared)
