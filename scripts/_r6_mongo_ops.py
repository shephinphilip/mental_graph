"""Round 6 Mongo inspect / staff seed / explain / integrity. No secret values printed."""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient

from services.users import hash_password


def client() -> MongoClient:
    uri = os.environ["MONGODB_URI"]
    return MongoClient(uri, serverSelectionTimeoutMS=8000)


def db_name() -> str:
    return os.environ.get("DATABASE_NAME") or os.environ.get("MONGO_DB_NAME") or "zenark_staging"


def summarize_plan(explain_doc: dict) -> dict:
    winning = (
        (explain_doc.get("queryPlanner") or {}).get("winningPlan")
        or explain_doc.get("winningPlan")
        or {}
    )
    stats = explain_doc.get("executionStats") or {}

    def walk(node: dict, acc: list[str]) -> None:
        if not isinstance(node, dict):
            return
        stage = str(node.get("stage") or "")
        if stage:
            acc.append(stage)
            ix = node.get("indexName")
            if ix:
                acc[-1] = f"{stage}:{ix}"
        for key in ("inputStage", "winningPlan"):
            if key in node:
                walk(node[key], acc)
        for child in node.get("inputStages") or []:
            walk(child, acc)

    stages: list[str] = []
    walk(winning, stages)
    return {
        "stages": stages,
        "nReturned": stats.get("nReturned"),
        "totalDocsExamined": stats.get("totalDocsExamined"),
        "totalKeysExamined": stats.get("totalKeysExamined"),
        "executionTimeMillis": stats.get("executionTimeMillis"),
        "collection_scan": "COLLSCAN" in stages,
        "index_scan": any(s.startswith("IXSCAN") for s in stages),
    }


def cmd_precheck() -> None:
    mongo = client()
    names = mongo.list_database_names()
    target = mongo[db_name()]
    print(
        json.dumps(
            {
                "databases": names,
                "has_mental_health": "mental_health" in names,
                "staging_db": db_name(),
                "collections": sorted(target.list_collection_names()),
                "user_count": target["users"].count_documents({}),
            },
            indent=2,
        )
    )


def cmd_seed_staff() -> None:
    stamp = uuid.uuid4().hex[:8]
    mongo = client()
    users = mongo[db_name()]["users"]
    now = datetime.now(timezone.utc)
    password = "R6-Staff-Disposable-9x!"
    rows = []
    for school_id, school, suffix in (
        ("r6_school_a", "R6 School A", "a"),
        ("r6_school_b", "R6 School B", "b"),
    ):
        user_id = f"usr_r6p{suffix}{stamp}"
        email = f"r6.principal.{suffix}.{stamp}@staging.zenark.test"
        users.insert_one(
            {
                "user_id": user_id,
                "email": email,
                "name": f"R6 Principal {suffix.upper()}",
                "password": hash_password(password),
                "isActive": True,
                "roles": ["principal"],
                "school_id": school_id,
                "school": school,
                "created_at": now,
                "r6_fixture": True,
            }
        )
        rows.append(
            {
                "user_id": user_id,
                "email": email,
                "school_id": school_id,
                "password": password,
            }
        )
    print(json.dumps({"staff": rows}, indent=2))


def cmd_attach_school() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    mongo = client()
    users = mongo[db_name()]["users"]
    result = users.update_one(
        {"user_id": payload["user_id"]},
        {
            "$set": {
                "school_id": payload["school_id"],
                "school": payload["school"],
                "class": payload.get("klass") or "10",
                "grade": payload.get("grade") or "10",
                "r6_fixture": True,
            }
        },
    )
    print(json.dumps({"matched": result.matched_count, "modified": result.modified_count}))


def cmd_encryption() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    mongo = client()
    db = mongo[db_name()]
    journal = db["journal_entries"].find_one({"user_id": payload["user_id"]}, sort=[("_id", -1)])
    mood = db["mood_logs"].find_one({"user_id": payload["user_id"]}, sort=[("_id", -1)])
    message = db["messages"].find_one({"user_id": payload["user_id"]}, sort=[("_id", -1)])
    marker = payload.get("marker") or ""

    def inspect(doc, field: str) -> dict:
        if not doc:
            return {"present": False}
        raw = str(doc.get(field) or "")
        return {
            "present": True,
            "enc_prefix": raw.startswith("enc::"),
            "contains_marker": bool(marker) and marker in raw,
            "len": len(raw),
        }

    print(
        json.dumps(
            {
                "journal_content": inspect(journal, "content"),
                "mood_note": inspect(mood, "note") if mood else {"present": False},
                "message_content": inspect(message, "content"),
            }
        )
    )


def _explain(db, collection: str, filter_doc: dict, sort: list | None = None) -> dict:
    spec: dict = {"find": collection, "filter": filter_doc}
    if sort:
        spec["sort"] = dict(sort)
    return db.command({"explain": spec, "verbosity": "executionStats"})


def cmd_explain() -> None:
    mongo = client()
    db = mongo[db_name()]
    user_id = (json.loads(sys.stdin.read() or "{}") or {}).get("user_id") or "__none__"
    plans = {
        "users_by_user_id": summarize_plan(_explain(db, "users", {"user_id": user_id})),
        "users_by_school_id": summarize_plan(
            _explain(db, "users", {"school_id": "r6_school_a", "isActive": True})
        ),
        "messages_by_user_session": summarize_plan(
            _explain(
                db,
                "messages",
                {"user_id": user_id, "session_id": {"$exists": True}},
                sort=[("seq", -1)],
            )
        ),
        "journal_by_user": summarize_plan(
            _explain(db, "journal_entries", {"user_id": user_id}, sort=[("timestamp", -1)])
        ),
        "graph_nodes_by_user": summarize_plan(_explain(db, "graph_nodes", {"user_id": user_id})),
        "profiles_by_user": summarize_plan(
            _explain(db, "student_psychological_profiles", {"user_id": user_id})
        ),
        "erasure_jobs_by_user": summarize_plan(
            _explain(db, "erasure_jobs", {"user_id": user_id})
        ),
    }
    print(json.dumps(plans, indent=2))


def cmd_seed_owned() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    user_id = payload["user_id"]
    mongo = client()
    db = mongo[db_name()]
    now = datetime.now(timezone.utc)
    extras = {
        "graph_nodes": {"user_id": user_id, "label": "R6_NODE", "r6_fixture": True},
        "graph_relationships": {
            "user_id": user_id,
            "type": "R6_REL",
            "r6_fixture": True,
        },
        "apm_nodes": {"user_id": user_id, "node_type": "CONTEXT", "r6_fixture": True},
        "apm_edges": {"user_id": user_id, "r6_fixture": True},
        "apm_events": {"user_id": user_id, "r6_fixture": True},
        "exam_buddy_nodes": {"user_id": user_id, "r6_fixture": True},
        "exam_buddy_relationships": {"user_id": user_id, "r6_fixture": True},
        "student_memories": {"user_id": user_id, "r6_fixture": True},
        "user_patterns": {"user_id": user_id, "r6_fixture": True},
        "pattern_evidence": {"user_id": user_id, "r6_fixture": True},
        "user_insights": {"user_id": user_id, "r6_fixture": True},
        "session_reports": {"user_id": user_id, "r6_fixture": True},
        "user_risk_turns": {"user_id": user_id, "r6_fixture": True},
        "gds_snapshots": {"user_id": user_id, "r6_fixture": True},
        "escalation_cases": {
            "user_id": user_id,
            "narrative": "r6-synthetic-narrative",
            "body": "r6-synthetic-body",
            "transcript": "r6-synthetic-transcript",
            "r6_fixture": True,
            "created_at": now,
        },
    }
    inserted = {}
    for name, doc in extras.items():
        db[name].insert_one(doc)
        inserted[name] = 1
    print(json.dumps({"inserted": inserted}))


def cmd_counts() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    user_ids = payload.get("user_ids") or []
    mongo = client()
    db = mongo[db_name()]
    names = [
        "messages",
        "journal_entries",
        "sleep_logs",
        "mood_logs",
        "daily_tasks",
        "habit_events",
        "meditation_executions",
        "meditation_offers",
        "student_psychological_profiles",
        "student_memories",
        "user_patterns",
        "pattern_evidence",
        "user_insights",
        "session_reports",
        "apm_nodes",
        "apm_edges",
        "apm_events",
        "graph_nodes",
        "graph_relationships",
        "exam_buddy_nodes",
        "exam_buddy_relationships",
        "gds_snapshots",
        "user_risk_turns",
        "escalation_cases",
        "erasure_jobs",
        "users",
    ]
    per_user = {}
    for uid in user_ids:
        row = {}
        for name in names:
            field = "user_id"
            row[name] = db[name].count_documents({field: uid})
        esc = db["escalation_cases"].find_one({"user_id": uid}) or {}
        row["escalation_narrative_present"] = bool(esc.get("narrative") or esc.get("body"))
        per_user[uid] = row
    plaintext_hits = 0
    enc_ok = 0
    for coll, field in (("journal_entries", "content"), ("messages", "content")):
        for doc in db[coll].find({}, {field: 1, "user_id": 1}).limit(200):
            raw = str(doc.get(field) or "")
            if not raw:
                continue
            if raw.startswith("enc::"):
                enc_ok += 1
            else:
                plaintext_hits += 1
    incomplete = db["messages"].count_documents({"incomplete": True})
    print(
        json.dumps(
            {
                "databases": mongo.list_database_names(),
                "has_mental_health": "mental_health" in mongo.list_database_names(),
                "user_count": db["users"].count_documents({}),
                "r6_users": db["users"].count_documents(
                    {"email": {"$regex": "@staging.zenark.test$"}}
                ),
                "encrypted_ok": enc_ok,
                "plaintext_sensitive_fields": plaintext_hits,
                "incomplete_messages": incomplete,
                "per_user": per_user,
            },
            indent=2,
        )
    )


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "precheck"
    {
        "precheck": cmd_precheck,
        "seed_staff": cmd_seed_staff,
        "attach_school": cmd_attach_school,
        "encryption": cmd_encryption,
        "explain": cmd_explain,
        "seed_owned": cmd_seed_owned,
        "counts": cmd_counts,
    }[cmd]()


if __name__ == "__main__":
    main()
