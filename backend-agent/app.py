"""
Compatibility bootstrap.

The FastAPI application is created in ``api.application.create_app``.
This module keeps ``from app import app`` and ``uvicorn app:app`` working
for ``python run.py``, existing tests, and Streamlit.
"""

from api.application import create_app
from api.deps import assert_owner as _assert_owner
from api.deps import authenticated_user_id
from api.deps import task_http as _task_http

app = create_app()

__all__ = ["app", "authenticated_user_id", "_assert_owner", "_task_http"]
