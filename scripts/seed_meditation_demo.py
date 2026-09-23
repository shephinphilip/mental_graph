"""
Seed five meditation demo profiles.

Idempotent. Re-running upserts the same users, executions, and recovery edges.

    python scripts/seed_meditation_demo.py
    python scripts/seed_meditation_demo.py --reset

Password for every demo account: Zenark@123

These are demonstration profiles, not clinical records.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pymongo import MongoClient

from config import get_settings
from meditation.data import get_session_by_id
from services.apm import make_apm_edge_id, make_apm_node_id
from services.meditation.demo_profiles import DEMO_PASSWORD, DEMO_PROFILES
from services.meditation.engine import recommend
from services.users import hash_password
from schemas import APMNodeType, APMRelationType


def _executions(profile: dict, now: datetime) -> list:
    rows = []
    for index, meditation_id in enumerate(profile["helpful_meditation_ids"]):
        session = get_session_by_id(meditation_id) or {}
        started = now - timedelta(days=14 + index * 3)
        nonce = f"seed-{profile['user_id']}-{meditation_id}-{index}"
        rows.append(
            {
                "execution_id": f"mexe_seed_{profile['user_id']}_{index}",
                "user_id": profile["user_id"],
                "meditation_id": meditation_id,
                "technique": session.get("technique"),
                "pre_state": {
                    "latent_state": (session.get("target_latent_states") or ["unspecified"])[0],
                    "confidence": 0.7,
                },
                "status": "COMPLETED",
                "listen_duration_seconds": session.get("duration_seconds") or 0,
                "user_helpfulness_feedback": "HELPFUL",
                "execution_nonce": nonce,
                "started_at": started,
                "completed_at": started + timedelta(minutes=4),
            }
        )
    return rows


def _print_recommendations(now: datetime) -> None:
    print("\nRanked practice for each demo probe (not hardcoded per user):\n")
    for profile in DEMO_PROFILES:
        decision = recommend(
            profile["probe_message"],
            preferred_language=profile["preferred_language"],
            sleep_text=profile["sleep_summary"],
            academic_text=profile["academic_summary"],
            attendance_text=profile["attendance_summary"],
            task_text=profile["task_summary"],
            journal_text=profile["journal_summary"],
            mood_text=" ".join(profile["moods"]),
            executions=_executions(profile, now),
            apm_success={
                meditation_id: 1.0
                for meditation_id in set(profile["helpful_meditation_ids"])
            },
            now=now,
        )
        session = decision.session or {}
        print(f"{profile['name']} ({profile['email']})")
        print(f"  probe: {profile['probe_message']}")
        print(f"  decision: {decision.decision}  id={session.get('id')}")
        print(f"  title: {session.get('title')}")
        print(f"  why: {decision.user_reason}")
        if decision.breakdown:
            print(f"  score: {decision.breakdown.final_score:.3f}")
        print()


def seed(reset: bool = False) -> None:
    settings = get_settings()
    client = MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=8000)
    db = client[settings.DATABASE_NAME]
    now = datetime.now(timezone.utc)
    password_hash = hash_password(DEMO_PASSWORD)
    user_ids = [profile["user_id"] for profile in DEMO_PROFILES]

    if reset:
        for name in (
            "meditation_executions",
            "meditation_offers",
            "mood_logs",
            "apm_edges",
            "apm_nodes",
            "apm_events",
        ):
            db[name].delete_many({"user_id": {"$in": user_ids}})

    for profile in DEMO_PROFILES:
        db["users"].update_one(
            {"email": profile["email"]},
            {
                "$set": {
                    "user_id": profile["user_id"],
                    "email": profile["email"],
                    "name": profile["name"],
                    "password": password_hash,
                    "class": profile["class"],
                    "school": profile["school"],
                    "age": profile["age"],
                    "isActive": True,
                    "roles": ["student"],
                    "preferred_language": profile["preferred_language"],
                    "personalization_consent": True,
                    "chief_concern": profile["chief_concern"],
                    "sleep_summary": profile["sleep_summary"],
                    "academic_summary": profile["academic_summary"],
                    "attendance_summary": profile["attendance_summary"],
                    "task_summary": profile["task_summary"],
                    "journal_summary": profile["journal_summary"],
                    "demo_probe_message": profile["probe_message"],
                    "changedPass": False,
                }
            },
            upsert=True,
        )
        for index, mood in enumerate(profile["moods"]):
            db["mood_logs"].update_one(
                {"user_id": profile["user_id"], "seed_key": f"{profile['user_id']}-mood-{index}"},
                {
                    "$set": {
                        "user_id": profile["user_id"],
                        "mood": mood,
                        "note": profile["journal_summary"],
                        "logged_at": now - timedelta(days=index + 1),
                        "seed_key": f"{profile['user_id']}-mood-{index}",
                    }
                },
                upsert=True,
            )
        for row in _executions(profile, now):
            db["meditation_executions"].update_one(
                {"user_id": row["user_id"], "execution_nonce": row["execution_nonce"]},
                {"$set": row},
                upsert=True,
            )
        for meditation_id in set(profile["helpful_meditation_ids"]):
            source = make_apm_node_id(
                profile["user_id"], APMNodeType.LATENT_STATE, "practiced state"
            )
            target = make_apm_node_id(
                profile["user_id"], APMNodeType.INTERVENTION, f"meditation:{meditation_id}"
            )
            edge_id = make_apm_edge_id(
                profile["user_id"], source, APMRelationType.RECOVERED_BY, target
            )
            db["apm_nodes"].update_one(
                {"user_id": profile["user_id"], "node_id": target},
                {
                    "$set": {
                        "user_id": profile["user_id"],
                        "node_id": target,
                        "node_type": APMNodeType.INTERVENTION.value,
                        "canonical_label": f"meditation:{meditation_id}",
                        "display_label": f"Meditation {meditation_id}",
                        "confidence_score": 0.8,
                    }
                },
                upsert=True,
            )
            db["apm_edges"].update_one(
                {"user_id": profile["user_id"], "edge_id": edge_id},
                {
                    "$set": {
                        "user_id": profile["user_id"],
                        "edge_id": edge_id,
                        "source_node_id": source,
                        "target_node_id": target,
                        "relation_type": APMRelationType.RECOVERED_BY.value,
                        "meditation_id": meditation_id,
                        "explicit_successes": 3,
                        "explicit_failures": 0,
                        "bayesian_score": 5 / 7,
                        "confidence_score": 0.8,
                        "last_outcome": "SUCCESS",
                        "updated_at": now,
                    }
                },
                upsert=True,
            )

    _print_recommendations(now)
    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    seed(reset=args.reset)
