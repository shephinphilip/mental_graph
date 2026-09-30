"""Local compatibility composer.

``python run.py`` and ``uvicorn app:app`` from the repository root keep the
historical single process: mental-health routes and the school dashboard.
Staging does not use this module. It starts backend-agent, backend-core, and
dashboard as separate containers and routes ``/api/v1/dashboard`` at the edge.

The canonical application modules are ``backend-agent/app.py`` and
``dashboard/dashboard_app.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Depends

_ROOT = Path(__file__).resolve().parent
for _path in (_ROOT / "dashboard", _ROOT / "backend-core", _ROOT / "backend-agent"):
    _text = str(_path)
    if _text in sys.path:
        sys.path.remove(_text)
    sys.path.insert(0, _text)

from api.application import create_app
from core.rate_limit import enforce_rate_limit
from dashboard.indexes import ensure_dashboard_indexes
from dashboard.router import router as dashboard_router
from db.indexes import register_index_hook

register_index_hook(ensure_dashboard_indexes)

app = create_app()
app.include_router(
    dashboard_router,
    prefix="/api/v1",
    dependencies=[Depends(enforce_rate_limit)],
)
