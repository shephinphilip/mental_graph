"""Round 6 follow-up: dashboard, erasure, encryption inspect glue. No JWT printing."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
import time
from typing import Any, Optional

BASE = os.environ.get("R6_BASE", "http://127.0.0.1:8000")
SECRETS = os.environ.get("R6_SECRETS", os.path.join(os.environ.get("TEMP") or "/tmp", "zenark_r6_secrets.json"))
OUT = os.environ.get("R6_FOLLOW", os.path.join(os.path.dirname(__file__), "..", "docs", "_r6_followup.json"))


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


def rid(headers: dict) -> str:
    return headers.get("X-Request-ID") or headers.get("x-request-id") or ""


def call(method: str, path: str, *, token: Optional[str] = None, json_body: Any = None, timeout: int = 45) -> Response:
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def login(email: str, password: str) -> Response:
    resp = call("POST", "/auth/login", json_body={"email": email, "password": password})
    if resp.status == 429:
        time.sleep(61)
        resp = call("POST", "/auth/login", json_body={"email": email, "password": password})
    return resp


def main() -> None:
    with open(SECRETS, encoding="utf-8") as handle:
        secrets = json.load(handle)
    users = secrets["users"]
    report: dict[str, Any] = {}

    enc = docker_python(
        ["encryption"],
        json.dumps({"user_id": users["c"]["user_id"], "marker": "R6ENC-MARKER-ALPHA"}),
    )
    report["encryption_mongo"] = enc

    staff = docker_python(["seed_staff"])
    report["staff_created"] = [
        {"user_id": row["user_id"], "email": row["email"], "school_id": row["school_id"]}
        for row in staff["staff"]
    ]
    staff_pass = staff["staff"][0]["password"]
    principal_a = staff["staff"][0]
    principal_b = staff["staff"][1]

    docker_python(
        ["attach_school"],
        json.dumps(
            {
                "user_id": users["sa"]["user_id"],
                "school_id": "r6_school_a",
                "school": "R6 School A",
            }
        ),
    )
    docker_python(
        ["attach_school"],
        json.dumps(
            {
                "user_id": users["sb"]["user_id"],
                "school_id": "r6_school_b",
                "school": "R6 School B",
            }
        ),
    )

    login_a = login(principal_a["email"], staff_pass)
    login_b = login(principal_b["email"], staff_pass)
    token_a = (login_a.json() or {}).get("access_token")
    token_b = (login_b.json() or {}).get("access_token")
    ctx_a = call("GET", "/api/v1/dashboard/context", token=token_a)
    ctx_b = call("GET", "/api/v1/dashboard/context", token=token_b)
    overview_a = call("GET", "/api/v1/dashboard/overview?school_id=r6_school_b", token=token_a)
    overview_b = call("GET", "/api/v1/dashboard/overview?school_id=r6_school_a", token=token_b)
    prof_cross = call(
        "GET",
        f"/api/v1/dashboard/students/{users['sb']['user_id']}/profile",
        token=token_a,
    )
    prof_own = call(
        "GET",
        f"/api/v1/dashboard/students/{users['sa']['user_id']}/profile",
        token=token_a,
    )
    interventions_cross = call(
        "GET",
        f"/api/v1/dashboard/students/{users['sb']['user_id']}/interventions",
        token=token_a,
    )
    student_ctx = call("GET", "/api/v1/dashboard/context", token=users["a"]["token"])

    def school_from(resp: Response) -> Any:
        body = resp.json() or {}
        data = body.get("data") or {}
        if isinstance(data, dict):
            return data.get("school_id") or data.get("school") or list(data.keys())[:8]
        return type(data).__name__

    report["dashboard"] = {
        "principal_a_login": login_a.status,
        "principal_b_login": login_b.status,
        "ctx_a": ctx_a.status,
        "ctx_b": ctx_b.status,
        "ctx_a_rid": rid(ctx_a.headers),
        "overview_a_with_foreign_query": overview_a.status,
        "overview_b_with_foreign_query": overview_b.status,
        "overview_a_ms": overview_a.latency_ms,
        "cross_profile": prof_cross.status,
        "own_profile": prof_own.status,
        "cross_interventions": interventions_cross.status,
        "student_ctx": student_ctx.status,
        "ctx_a_shape": school_from(ctx_a),
        "ctx_b_shape": school_from(ctx_b),
        "overview_a_shape": school_from(overview_a),
        "leaks_overview": [
            flag
            for flag in ("akia", "bearer ", "mongodb://")
            if flag in overview_a.text.lower()
        ],
    }

    docker_python(["seed_owned"], json.dumps({"user_id": users["d"]["user_id"]}))
    before = docker_python(
        ["counts"],
        json.dumps({"user_ids": [users["d"]["user_id"], users["e"]["user_id"]]}),
    )
    erase1 = call("POST", "/api/memory/erasure", token=users["d"]["token"])
    erase2 = call("POST", "/api/memory/erasure", token=users["d"]["token"])
    after = docker_python(
        ["counts"],
        json.dumps({"user_ids": [users["d"]["user_id"], users["e"]["user_id"]]}),
    )
    dead_journal = call("GET", "/journal/recent-entries", token=users["d"]["token"])
    live_journal = call("GET", "/journal/recent-entries", token=users["e"]["token"])
    dead_chat = call(
        "GET",
        f"/chat/session/{users['d']['user_id']}/resume",
        token=users["d"]["token"],
    )
    report["erasure"] = {
        "erase1_http": erase1.status,
        "erase1_status": (erase1.json() or {}).get("status"),
        "erase1_counts": (erase1.json() or {}).get("counts"),
        "erase2_http": erase2.status,
        "erase2_status": (erase2.json() or {}).get("status"),
        "erase2_job_id_same": (erase1.json() or {}).get("job_id")
        == (erase2.json() or {}).get("job_id"),
        "before_d": before.get("per_user", {}).get(users["d"]["user_id"]),
        "after_d": after.get("per_user", {}).get(users["d"]["user_id"]),
        "after_e": after.get("per_user", {}).get(users["e"]["user_id"]),
        "d_recent_journal_http": dead_journal.status,
        "d_recent_count": len(((dead_journal.json() or {}).get("entries") or [])),
        "e_recent_journal_http": live_journal.status,
        "e_recent_count": len(((live_journal.json() or {}).get("entries") or [])),
        "d_resume_http": dead_chat.status,
        "integrity": {
            "has_mental_health": after.get("has_mental_health"),
            "encrypted_ok": after.get("encrypted_ok"),
            "plaintext_sensitive_fields": after.get("plaintext_sensitive_fields"),
            "user_count": after.get("user_count"),
            "r6_users": after.get("r6_users"),
            "incomplete_messages": after.get("incomplete_messages"),
        },
    }

    explain = docker_python(["explain"], json.dumps({"user_id": users["c"]["user_id"]}))
    report["explain"] = explain

    consent_counts = docker_python(
        ["counts"],
        json.dumps({"user_ids": [users["g"]["user_id"], users["h"]["user_id"]]}),
    )
    report["personalization_counts"] = consent_counts.get("per_user")

    profile_g = call("GET", "/api/memory/profile", token=users["g"]["token"])
    profile_h = call("GET", "/api/memory/profile", token=users["h"]["token"])

    def profile_safe(resp: Response) -> dict:
        body = resp.json() or {}
        conversations = body.get("conversations") or {}
        journaling = body.get("journaling") or {}
        blob = json.dumps(body)
        return {
            "http": resp.status,
            "user_id": body.get("user_id"),
            "conversation_keys": list(conversations.keys())[:12] if isinstance(conversations, dict) else type(conversations).__name__,
            "journaling_keys": list(journaling.keys())[:12] if isinstance(journaling, dict) else type(journaling).__name__,
            "has_enc_prefix": "enc::" in blob,
            "has_crisis_fixture": "want to die" in blob.lower(),
            "has_journal_marker": "R6ENC-MARKER" in blob,
        }

    report["profiles"] = {
        "g": profile_safe(profile_g),
        "h": profile_safe(profile_h),
        "g_is_g": (profile_g.json() or {}).get("user_id") == users["g"]["user_id"],
        "h_is_h": (profile_h.json() or {}).get("user_id") == users["h"]["user_id"],
        "cross": (profile_g.json() or {}).get("user_id") != users["h"]["user_id"],
    }

    os.makedirs(os.path.dirname(os.path.abspath(OUT)), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print(json.dumps({"wrote": os.path.abspath(OUT), "dashboard_student": student_ctx.status, "erase1": erase1.status}))


if __name__ == "__main__":
    main()
