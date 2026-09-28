"""Clean erasure on a user who has not already succeeded an erasure job."""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Optional

BASE = os.environ.get("R6_BASE", "http://127.0.0.1:8000")
SECRETS = os.environ.get("R6_SECRETS", os.path.join(os.environ.get("TEMP") or "/tmp", "zenark_r6_secrets.json"))
OUT = os.environ.get("R6_ERASE", os.path.join(os.path.dirname(__file__), "..", "docs", "_r6_erasure_clean.json"))


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


def call(method: str, path: str, *, token: Optional[str] = None, json_body: Any = None) -> Response:
    hdrs = {"Accept": "application/json"}
    payload = None
    if json_body is not None:
        payload = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=payload, headers=hdrs, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return Response(resp.status, dict(resp.headers), resp.read(), (time.perf_counter() - started) * 1000)
    except urllib.error.HTTPError as exc:
        return Response(exc.code, dict(exc.headers), exc.read(), (time.perf_counter() - started) * 1000)


def docker_python(args: list[str], stdin: str = "") -> dict:
    cmd = [
        "docker",
        "exec",
        "-i",
        "-e",
        "PYTHONPATH=/app",
        "-w",
        "/app",
        "zenark-staging-api",
        "python",
        "/tmp/_r6_mongo_ops.py",
        *args,
    ]
    proc = subprocess.run(cmd, input=stdin.encode("utf-8") if stdin else None, capture_output=True)
    text = proc.stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace") or text)
    return json.loads(text) if text.strip() else {}


def owned_nonzero(row: dict) -> dict:
    skip = {"users", "erasure_jobs", "escalation_cases", "escalation_narrative_present"}
    return {k: v for k, v in row.items() if k not in skip and isinstance(v, int) and v > 0}


def main() -> None:
    with open(SECRETS, encoding="utf-8") as handle:
        secrets = json.load(handle)
    target = secrets["users"]["f"]
    other = secrets["users"]["e"]
    docker_python(["seed_owned"], json.dumps({"user_id": target["user_id"]}))
    before = docker_python(
        ["counts"], json.dumps({"user_ids": [target["user_id"], other["user_id"]]})
    )
    erase1 = call("POST", "/api/memory/erasure", token=target["token"])
    erase2 = call("POST", "/api/memory/erasure", token=target["token"])
    after = docker_python(
        ["counts"], json.dumps({"user_ids": [target["user_id"], other["user_id"]]})
    )
    journals = call("GET", "/journal/recent-entries", token=target["token"])
    other_journals = call("GET", "/journal/recent-entries", token=other["token"])
    before_t = before["per_user"][target["user_id"]]
    after_t = after["per_user"][target["user_id"]]
    leftover = owned_nonzero(after_t)
    report = {
        "target": target["user_id"],
        "erase1_http": erase1.status,
        "erase1_status": (erase1.json() or {}).get("status"),
        "erase1_counts": (erase1.json() or {}).get("counts"),
        "erase2_http": erase2.status,
        "erase2_same_job": (erase1.json() or {}).get("job_id") == (erase2.json() or {}).get("job_id"),
        "before_owned_nonzero": owned_nonzero(before_t),
        "after_owned_nonzero": leftover,
        "after_users_row": after_t.get("users"),
        "after_erasure_jobs": after_t.get("erasure_jobs"),
        "after_escalation_narrative": after_t.get("escalation_narrative_present"),
        "target_journal_count": len(((journals.json() or {}).get("entries") or [])),
        "other_journal_count": len(((other_journals.json() or {}).get("entries") or [])),
        "other_after_nonzero": owned_nonzero(after["per_user"][other["user_id"]]),
        "clean": leftover == {},
    }
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({"wrote": os.path.abspath(OUT), "clean": report["clean"], "leftover_keys": list(leftover)}))


if __name__ == "__main__":
    main()
