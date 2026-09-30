"""500 responses must not include exception text, paths, or provider names."""

import asyncio

from fastapi import HTTPException

from core.exceptions import http_exception_handler


class _Req:
    state = type("S", (), {"request_id": "abc123"})()


def test_handler_sanitizes_500_payload():
    response = asyncio.get_event_loop().run_until_complete(
        http_exception_handler(
            _Req(),
            HTTPException(status_code=500, detail="Secret AWS key leaked /home/app/file.py"),
        )
    )
    payload = response.body.decode()
    assert "AWS" not in payload
    assert "/home/" not in payload
    assert "INTERNAL_ERROR" in payload
    assert "An unexpected error occurred." in payload
