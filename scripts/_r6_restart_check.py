"""Restart recovery checks. Does not print tokens."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Optional

BASE = os.environ.get("R6_BASE", "http://127.0.0.1:8000")
SECRETS = os.environ.get("R6_SECRETS", os.path.join(os.environ.get("TEMP") or "/tmp", "zenark_r6_secrets.json"))
OUT = os.environ.get("R6_RESTART", os.path.join(os.path.dirname(__file__), "..", "docs", "_r6_restart.json"))


class Response:
    def __init__(self, status: int, headers: dict, body: bytes, latency_ms: float):
        self.status = status
        self.headers = {str(k): str(v) for k, v in headers.items()}
        self.body = body
        self.latency_ms = round(latency_ms, 1)
        self.text = body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.text) if self.text else None
        except json.JSONDecodeError:
            return None


def call(method: str, path: str, *, token: Optional[str] = None) -> Response:
    hdrs = {"Accept": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, headers=hdrs, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return Response(resp.status, dict(resp.headers), resp.read(), (time.perf_counter() - started) * 1000)
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers), exc.read(), (time.perf_counter() - started) * 1000)


def wait_ready(timeout: int = 90) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            live = call("GET", "/health/live")
            ready = call("GET", "/health/ready")
            last = {
                "live": live.status,
                "ready": ready.status,
                "ready_status": (ready.json() or {}).get("status"),
                "live_ms": live.latency_ms,
                "ready_ms": ready.latency_ms,
            }
            if live.status == 200 and ready.status == 200:
                return last
        except Exception as exc:
            last = {"error": type(exc).__name__}
        time.sleep(2)
    return last or {"error": "timeout"}


def main() -> None:
    with open(SECRETS, encoding="utf-8") as handle:
        secrets = json.load(handle)
    token = secrets["users"]["e"]["token"]
    user_id = secrets["users"]["e"]["user_id"]
    ready = wait_ready()
    persist = call("GET", "/journal/recent-entries", token=token)
    persist_login = None
    report = {
        "ready": ready,
        "journal_http": persist.status,
        "journal_count": len(((persist.json() or {}).get("entries") or [])),
        "user_id": user_id,
        "login_still_works": None,
    }
    # token may still be valid; also login
    import urllib.request as u

    payload = json.dumps({"email": secrets["users"]["e"]["email"], "password": secrets["password"]}).encode()
    req = urllib.request.Request(
        BASE + "/auth/login",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            persist_login = Response(resp.status, dict(resp.headers), resp.read(), (time.perf_counter() - started) * 1000)
    except urllib.error.HTTPError as exc:
        persist_login = Response(exc.code, dict(exc.headers), exc.read(), (time.perf_counter() - started) * 1000)
    report["login_still_works"] = persist_login.status if persist_login else None
    report["login_has_token"] = bool((persist_login.json() or {}).get("access_token")) if persist_login else False
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({k: report[k] for k in report if k != "user_id"} | {"user_id_suffix": user_id[-6:]}))


if __name__ == "__main__":
    main()
