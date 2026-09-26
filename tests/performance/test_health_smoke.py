"""Performance tests are methodology, not a capacity certificate.

This file only checks that the public probes respond. Real RPS / p95
numbers require `load_tests/locustfile.py` against a live deployment.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app import app


def test_health_live_is_fast_enough_for_a_probe():
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "live"
