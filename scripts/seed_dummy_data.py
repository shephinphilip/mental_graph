"""
scripts/seed_dummy_data.py — Insert demo users, marks, and one prior chat.

Idempotent: upserts by email / student_id / session_id.

Demo password for every seeded student: Zenark@123
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pymongo import MongoClient

from config import get_settings
from services.users import hash_password


DEMO_PASSWORD = "Zenark@123"


def _dt(days_ago: int, hour: int = 10) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hour)


def seed() -> None:
    settings = get_settings()
    client = MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=8000)
    db = client[settings.DATABASE_NAME]

    db["users"].create_index("email", unique=True)
    db["users"].create_index("user_id", unique=True)
    db["marks"].create_index([("student_id", 1), ("exam_date", -1)])
    db["messages"].create_index([("user_id", 1), ("created_at", -1)])
    db["messages"].create_index([("session_id", 1), ("created_at", -1)])

    password_hash = hash_password(DEMO_PASSWORD)
    now = datetime.now(timezone.utc)

    users = [
        {
            "user_id": "stu_aarav_001",
            "email": "aarav.sharma@zenark.demo",
            "name": "Aarav Sharma",
            "password": password_hash,
            "class": "12",
            "school": "Delhi Public School, R.K. Puram",
            "isActive": True,
            "roles": ["student"],
            "changedPass": False,
            "__v": 0,
            "preferred_language": "ENGLISH",
            "preferred_language_updated_at": now,
            "current_risk_level": "moderate",
            "personalization_consent": False,
            "age": 17,
            "chief_concern": "JEE pressure and slipping Physics scores",
            "board": "CBSE",
            "school_board": "CBSE",
            "memory_summary": (
                "Aarav is in Class 12, aiming for JEE. He has talked about sleep "
                "shrinking around mocks and feeling he is letting his parents down."
            ),
            "key_takeaways": [
                "Physics mocks have been sliding while Chemistry is steadier.",
                "Names his mother when talking about expectation.",
                "Uses late-night YouTube 'productivity' videos instead of sleeping.",
            ],
        },
        {
            "user_id": "stu_meera_002",
            "email": "meera.iyer@zenark.demo",
            "name": "Meera Iyer",
            "password": password_hash,
            "class": "10",
            "school": "St. Joseph's ICSE, Bengaluru",
            "isActive": True,
            "roles": ["student"],
            "changedPass": False,
            "__v": 0,
            "preferred_language": "ENGLISH",
            "preferred_language_updated_at": now,
            "current_risk_level": "low",
            "personalization_consent": False,
            "age": 15,
            "chief_concern": "Board exam anxiety with improving but uneven English",
            "board": "ICSE",
            "school_board": "ICSE",
            "memory_summary": (
                "Meera is in Class 10 ICSE. She worries about 'first boards' and "
                "compares herself to a cousin who scored 96%."
            ),
            "key_takeaways": [
                "Maths is a relative strength.",
                "Friendship group is protective; one teacher (Ms Rao) is a safe adult.",
            ],
        },
        {
            "user_id": "stu_kabir_003",
            "email": "kabir.khan@zenark.demo",
            "name": "Kabir Khan",
            "password": password_hash,
            "class": "11",
            "school": "Government Boys Senior Secondary, Lucknow",
            "isActive": True,
            "roles": ["student"],
            "changedPass": False,
            "__v": 0,
            "preferred_language": "HINDI",
            "preferred_language_updated_at": now,
            "current_risk_level": "low",
            "personalization_consent": False,
            "age": 16,
            "chief_concern": "Attendance dips around tests; family vs arts interest",
            "board": "UP Board",
            "school_board": "UP Board",
            "memory_summary": (
                "Kabir is in Class 11. He is pulled between family pressure toward "
                "engineering and a quieter interest in history and writing."
            ),
            "key_takeaways": [
                "Hindi and History hold up; Maths tests spike his avoidance.",
            ],
        },
    ]

    for user in users:
        db["users"].update_one({"email": user["email"]}, {"$set": user}, upsert=True)

    marks_docs = [
        # Aarav — declining Physics, mixed overall (JEE mock path)
        {"student_id": "stu_aarav_001", "subject": "Physics", "exam_type": "Unit Test", "marks": 64, "total_marks": 80, "percentage": 80.0, "rank": 12, "class": "12", "exam_date": _dt(80), "board": "CBSE"},
        {"student_id": "stu_aarav_001", "subject": "Chemistry", "exam_type": "Unit Test", "marks": 70, "total_marks": 80, "percentage": 87.5, "rank": 8, "class": "12", "exam_date": _dt(72), "board": "CBSE"},
        {"student_id": "stu_aarav_001", "subject": "Mathematics", "exam_type": "Unit Test", "marks": 68, "total_marks": 80, "percentage": 85.0, "rank": 10, "class": "12", "exam_date": _dt(70), "board": "CBSE"},
        {"student_id": "stu_aarav_001", "subject": "Physics", "exam_type": "JEE Mock", "marks": 42, "total_marks": 100, "percentage": 42.0, "rank": 1840, "class": "12", "exam_date": _dt(40), "board": "CBSE"},
        {"student_id": "stu_aarav_001", "subject": "Chemistry", "exam_type": "JEE Mock", "marks": 61, "total_marks": 100, "percentage": 61.0, "rank": 920, "class": "12", "exam_date": _dt(40), "board": "CBSE"},
        {"student_id": "stu_aarav_001", "subject": "Physics", "exam_type": "JEE Mock", "marks": 31, "total_marks": 100, "percentage": 31.0, "rank": 3102, "class": "12", "exam_date": _dt(12), "board": "CBSE"},
        {"student_id": "stu_aarav_001", "subject": "Mathematics", "exam_type": "JEE Mock", "marks": 54, "total_marks": 100, "percentage": 54.0, "rank": 1404, "class": "12", "exam_date": _dt(12), "board": "CBSE"},
        # Meera — improving
        {"student_id": "stu_meera_002", "subject": "Mathematics", "exam_type": "Unit Test", "marks": 62, "total_marks": 80, "percentage": 77.5, "rank": 6, "class": "10", "exam_date": _dt(60), "board": "ICSE"},
        {"student_id": "stu_meera_002", "subject": "English", "exam_type": "Unit Test", "marks": 54, "total_marks": 80, "percentage": 67.5, "rank": 14, "class": "10", "exam_date": _dt(50), "board": "ICSE"},
        {"student_id": "stu_meera_002", "subject": "Science", "exam_type": "Unit Test", "marks": 70, "total_marks": 80, "percentage": 87.5, "rank": 4, "class": "10", "exam_date": _dt(35), "board": "ICSE"},
        {"student_id": "stu_meera_002", "subject": "English", "exam_type": "Prelim", "marks": 64, "total_marks": 80, "percentage": 80.0, "rank": 9, "class": "10", "exam_date": _dt(14), "board": "ICSE"},
        {"student_id": "stu_meera_002", "subject": "Mathematics", "exam_type": "Prelim", "marks": 74, "total_marks": 80, "percentage": 92.5, "rank": 2, "class": "10", "exam_date": _dt(14), "board": "ICSE"},
        # Kabir — Maths dip, Humanities steady
        {"student_id": "stu_kabir_003", "subject": "History", "exam_type": "Unit Test", "marks": 78, "total_marks": 100, "percentage": 78.0, "rank": 5, "class": "11", "exam_date": _dt(45), "board": "UP Board"},
        {"student_id": "stu_kabir_003", "subject": "Mathematics", "exam_type": "Unit Test", "marks": 38, "total_marks": 100, "percentage": 38.0, "rank": 29, "class": "11", "exam_date": _dt(30), "board": "UP Board"},
        {"student_id": "stu_kabir_003", "subject": "Hindi", "exam_type": "Unit Test", "marks": 81, "total_marks": 100, "percentage": 81.0, "rank": 3, "class": "11", "exam_date": _dt(20), "board": "UP Board"},
        {"student_id": "stu_kabir_003", "subject": "Mathematics", "exam_type": "Half Yearly", "marks": 29, "total_marks": 100, "percentage": 29.0, "rank": 34, "class": "11", "exam_date": _dt(8), "board": "UP Board"},
    ]

    db["marks"].delete_many(
        {"student_id": {"$in": ["stu_aarav_001", "stu_meera_002", "stu_kabir_003"]}}
    )
    db["marks"].insert_many(marks_docs)

    prior_session = "session_aarav_prior_001"
    db["messages"].delete_many({"session_id": prior_session})
    prior_turns = [
        ("user", "Physics mock went badly again. I don't want to tell my parents."),
        (
            "assistant",
            "You sat with a result you already feared, and now you're carrying it alone. "
            "What feels hardest — the score itself, or imagining their faces when they hear?",
        ),
        (
            "user",
            "Both. Amma keeps saying we sacrificed everything for JEE. I slept maybe four hours.",
        ),
        (
            "assistant",
            "Four hours and a falling mock is a brutal combination — not a character verdict. "
            "We can look at Physics as one subject under strain, not as your whole worth. "
            "Would it be okay if next time we start from how last night actually went?",
        ),
    ]
    base = _dt(3, hour=2)
    for i, (role, content) in enumerate(prior_turns):
        db["messages"].insert_one(
            {
                "session_id": prior_session,
                "user_id": "stu_aarav_001",
                "role": role,
                "content": content,
                "created_at": base + timedelta(minutes=i * 4),
            }
        )

    print("Seeded users:")
    print("  aarav.sharma@zenark.demo  /  Zenark@123  (Class 12, prior chat exists)")
    print("  meera.iyer@zenark.demo    /  Zenark@123  (Class 10)")
    print("  kabir.khan@zenark.demo    /  Zenark@123  (Class 11)")
    print(f"Database: {settings.DATABASE_NAME}")
    client.close()


if __name__ == "__main__":
    seed()
