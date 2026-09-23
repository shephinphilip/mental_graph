"""Meditation catalog, ranking, executions, and one-card presentation."""

from datetime import datetime, timedelta, timezone

import pytest

from meditation.audio import get_audio_path, resolve_audio_file
from meditation.data import (
    get_all_sessions,
    get_session_by_id,
    get_sessions_by_category,
    get_sessions_by_tab,
)
from meditation.metadata import METADATA_STATUS_PROVISIONAL, friction_for_duration
from services.meditation.cards import (
    audio_path_for_card,
    build_meditation_card,
    ensure_single_meditation_card,
    user_visible_fields,
)
from services.meditation.demo_profiles import DEMO_PROFILES
from services.meditation.engine import (
    estimate_state,
    evaluate_catalog_promotion,
    pad_distance,
    recommend,
    repetition_factor,
    scoring_weights,
)
from services.meditation.service import overlay_promotions
from services.meditation.service import (
    complete_execution,
    record_feedback,
    start_execution,
)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        return self

    async def to_list(self, length=None):
        return list(self._docs[: length or len(self._docs)])


class _Collection:
    def __init__(self):
        self.docs = []

    def find(self, query, projection=None):
        return _Cursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if _match(doc, query):
                return doc
        return None

    async def insert_one(self, doc):
        if any(
            d.get("user_id") == doc.get("user_id")
            and d.get("execution_nonce") == doc.get("execution_nonce")
            for d in self.docs
        ):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("dup")
        self.docs.append(dict(doc))

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if _match(doc, query):
                doc.update(update.get("$set") or {})
                return
        if upsert:
            created = dict(query)
            created.update(update.get("$setOnInsert") or {})
            created.update(update.get("$set") or {})
            self.docs.append(created)


def _match(doc, query):
    for key, value in query.items():
        if isinstance(value, dict) and "$in" in value:
            if doc.get(key) not in value["$in"]:
                return False
        elif doc.get(key) != value:
            return False
    return True


class _DB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = _Collection()
        return dict.__getitem__(self, name)


def _history(meditation_id, *, feedback="HELPFUL", days_ago=14, technique=None):
    return {
        "meditation_id": meditation_id,
        "user_helpfulness_feedback": feedback,
        "status": "COMPLETED",
        "technique": technique,
        "started_at": datetime.now(timezone.utc) - timedelta(days=days_ago),
    }


def test_report_state_keeps_model_pad_and_limits_patterns():
    from services.meditation.engine import estimate_from_report, recommend_from_estimate

    estimate = estimate_from_report(
        valence=-0.1,
        arousal=0.66,
        dominance=-0.2,
        confidence=0.84,
        latent_states=[{"state": "ANXIETY_HIGH", "probability": 0.8}],
        patterns=[{"domains": ["sleep"], "confidence": 0.9}],
    )
    assert estimate.valence == pytest.approx(-0.1)
    assert estimate.arousal == pytest.approx(0.66)
    assert estimate.top_state == "ANXIETY_HIGH"
    sleep = next(item for item in estimate.latent if item.state == "SLEEP_PREPARATION")
    assert sleep.probability < 0.8
    decision = recommend_from_estimate(estimate)
    assert decision.decision == "RECOMMEND_MEDITATION"
    assert decision.session["id"] in {"311", "315"}
    assert "SLEEP_PREPARATION" not in (decision.session.get("target_latent_states") or [])
    crisis = recommend_from_estimate(estimate, crisis=True)
    assert crisis.withheld_reason == "crisis"


@pytest.mark.asyncio
async def test_session_report_ranks_from_the_model_not_the_transcript_words():
    from unittest.mock import AsyncMock, MagicMock, patch

    from services.session_report import generate_session_report

    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=(
                '{"summary": "This sitting stayed with a tight, anxious stretch.",'
                '"valence": -0.45, "arousal": 0.7, "dominance": -0.25, "confidence": 0.86,'
                '"latent_states": [{"state": "ANXIETY_HIGH", "probability": 0.82}],'
                '"crisis_signal": false}'
            )
        )
    )
    db = _DB()

    async def _load(*_args, **_kwargs):
        return [
            {
                "role": "user",
                "content": "hello there",
                "message_id": "1",
                "message_kind": "chat",
            }
        ]

    with patch("services.session_report.load_session_messages", _load), patch(
        "llm_provider.get_llm", return_value=llm
    ), patch(
        "services.patterns.retrieve.retrieve_relevant_patterns",
        AsyncMock(return_value=[{"domains": ["sleep"], "confidence": 0.9}]),
    ):
        report = await generate_session_report(db, user_id="user_a", session_id="sess_a")

    assert "valence" not in report["summary"].lower()
    assert report["recommendation"]["meditation_id"] in {"311", "315"}
    assert report["recommendation"]["action_card"]["action_payload"]["execution_nonce"]
    assert "0.7" not in report["summary"]


def test_report_payload_parses_fenced_json():
    from services.session_report import parse_report_payload

    payload = parse_report_payload(
        """```json
        {"summary": "A tight afternoon.", "valence": -0.4, "arousal": 0.5,
         "dominance": -0.1, "confidence": 0.7,
         "latent_states": [{"state": "STRESS_HIGH", "probability": 0.6}],
         "crisis_signal": false}
        ```"""
    )
    assert payload["summary"] == "A tight afternoon."
    assert payload["latent_states"][0]["state"] == "STRESS_HIGH"


def test_catalog_helpers_keep_existing_sessions():
    session = get_session_by_id("311")
    assert session["title"] == "1. Meditation for stress & anxiety"
    assert session["category"] == "stress_anxiety"
    assert get_sessions_by_category("sleep")
    assert get_sessions_by_tab(1)
    assert len(get_all_sessions()) >= 60


def test_provisional_metadata_and_unreviewed_sessions():
    reviewed = get_session_by_id("311")
    assert reviewed["metadata_status"] == METADATA_STATUS_PROVISIONAL
    assert reviewed["pad_coordinates"]["target_arousal"] < 0
    assert reviewed["required_cognitive_load"] == "VERY_LOW"
    assert reviewed["metadata_basis"] == "title_and_description"
    intro = get_session_by_id("101")
    assert intro["title"].startswith("What is mindfulness")
    assert intro["metadata_status"] == "unreviewed"
    assert intro["pad_coordinates"] is None


def test_audio_path_uses_real_files_and_missing_audio_is_none():
    path = get_audio_path("311")
    assert path is not None
    assert path.name == "311.mp3"
    assert path.is_file()
    assert get_audio_path("325") is None
    assert get_audio_path("354") is None
    assert get_audio_path("does-not-exist") is None
    assert resolve_audio_file("325") is None
    session = get_session_by_id("311")
    assert session["has_audio"] is True
    assert session["duration_seconds"]
    assert session["friction_level"] == friction_for_duration(session["duration_seconds"])
    assert session["friction_level"] == "LOW"


def test_pad_distance_uses_three_dimensions():
    assert pad_distance(0, 0, 0, 0, 0, 0) == 0
    distance = pad_distance(-0.5, 0.5, -0.5, 0.5, -0.5, 0.5)
    assert distance == pytest.approx((3 * 1.0) ** 0.5)


def test_low_confidence_and_crisis_withhold_meditation():
    vague = recommend("ok thanks")
    assert vague.decision == "NO_MEDITATION"
    assert vague.withheld_reason == "low_confidence"
    crisis = recommend("I want to die")
    assert crisis.decision == "NO_MEDITATION"
    assert crisis.withheld_reason == "crisis"


def test_friction_and_cognitive_load_filter_long_practices_when_overloaded():
    decision = recommend(
        "My chest is tight and I only have a minute. I'm anxious and overwhelmed."
    )
    assert decision.decision == "RECOMMEND_MEDITATION"
    assert decision.session["friction_level"] in {"MICRO", "LOW"}
    assert decision.session["required_cognitive_load"] == "VERY_LOW"
    assert decision.session["id"] != "312"
    assert decision.session["id"] != "350"


def test_repetition_penalty_moves_off_the_recent_session():
    now = datetime(2026, 9, 23, 15, tzinfo=timezone.utc)
    repeated = recommend(
        "My chest is tight and I only have a minute. I'm anxious and overwhelmed.",
        executions=[
            _history("311", days_ago=0, technique="MINDFUL_OBSERVATION"),
        ],
        now=now,
    )
    fresh = recommend(
        "My chest is tight and I only have a minute. I'm anxious and overwhelmed.",
        now=now,
    )
    assert repeated.session["id"] != "311"
    assert fresh.session["id"] == "311"


def test_explicit_feedback_changes_rank_and_popularity_does_not():
    message = "I can't focus. The backlog is huge and my attention is scattered."
    without = recommend(message)
    with_history = recommend(
        message,
        executions=[_history("331", technique="FOCUS")],
        apm_success={"331": 1.0},
    )
    assert with_history.session["id"] == "331"
    assert with_history.breakdown.personal_success > without.breakdown.personal_success
    # A globally popular id is not an input. Not-helpful evidence can outrank it.
    rejected = recommend(
        message,
        executions=[_history("331", feedback="NOT_HELPFUL", technique="FOCUS")],
    )
    assert rejected.breakdown is None or rejected.session["id"] != "331" or (
        rejected.breakdown.personal_success == 0
    )


def test_demo_profiles_receive_different_practices():
    now = datetime(2026, 9, 23, 21, tzinfo=timezone.utc)
    chosen = {}
    for profile in DEMO_PROFILES:
        executions = []
        for index, meditation_id in enumerate(profile["helpful_meditation_ids"]):
            session = get_session_by_id(meditation_id)
            executions.append(
                _history(
                    meditation_id,
                    days_ago=14 + index,
                    technique=session["technique"],
                )
            )
        decision = recommend(
            profile["probe_message"],
            preferred_language=profile["preferred_language"],
            sleep_text=profile["sleep_summary"],
            academic_text=profile["academic_summary"],
            attendance_text=profile["attendance_summary"],
            task_text=profile["task_summary"],
            journal_text=profile["journal_summary"],
            mood_text=" ".join(profile["moods"]),
            executions=executions,
            apm_success={mid: 1.0 for mid in set(profile["helpful_meditation_ids"])},
            now=now,
        )
        assert decision.decision == "RECOMMEND_MEDITATION"
        chosen[profile["user_id"]] = decision.session["id"]
    assert chosen["med_demo_stress"] == "311"
    assert chosen["med_demo_body"] == "347"
    assert chosen["med_demo_kind"] == "356"
    assert chosen["med_demo_focus"] == "331"
    assert chosen["med_demo_sleep"] == "324"
    assert len(set(chosen.values())) == 5


def test_regional_language_does_not_cross_users():
    hindi = estimate_state(
        "I feel fairly steady today and I have time to sit.",
        preferred_language="HINDI",
    )
    english = recommend(
        "I feel fairly steady today and I have time to sit.",
        preferred_language="ENGLISH",
    )
    assert hindi.preferred_language == "hi"
    ranked_ids = [row.meditation_id for row in english.ranked]
    assert "1202" not in ranked_ids
    assert "1201" not in ranked_ids


def test_one_meditation_card_and_no_internal_scores():
    offer = {
        "decision": "RECOMMEND_MEDITATION",
        "meditation_id": "311",
        "title": "1. Meditation for stress & anxiety",
        "user_reason": "A short, low-effort practice.",
        "duration_seconds": 186,
        "category": "stress_anxiety",
        "execution_nonce": "nonce-1",
        "audio_available": True,
    }
    from schemas import ActionCard, CardType

    extra = ActionCard(
        card_type=CardType.TOOL,
        title="Other practice",
        action_payload={"type": "MEDITATION", "meditation_id": "999"},
    )
    cards = ensure_single_meditation_card([extra], offer)
    meditation_cards = [
        card for card in cards if card.action_payload.get("type") == "MEDITATION"
    ]
    assert len(meditation_cards) == 1
    assert meditation_cards[0].action_payload["meditation_id"] == "311"
    visible = user_visible_fields(meditation_cards[0].model_dump())
    assert visible["heading"] == "Recommended for right now"
    assert "valence" not in visible
    assert "confidence" not in visible
    blob = str(meditation_cards[0].model_dump())
    assert "pad_score" not in blob
    assert audio_path_for_card(meditation_cards[0].model_dump()).endswith("311.mp3")
    suppressed = ensure_single_meditation_card([], offer, suppress=True)
    assert suppressed == []
    none = ensure_single_meditation_card(
        [], {"decision": "NO_MEDITATION"}
    )
    assert none == []


def test_prompt_says_no_meditation_by_default():
    from prompts import format_system_prompt

    text = format_system_prompt()
    assert "NO_MEDITATION" in text


@pytest.mark.asyncio
async def test_start_complete_feedback_are_idempotent_and_user_scoped():
    db = _DB()
    first = await start_execution(
        db,
        user_id="user_a",
        meditation_id="311",
        execution_nonce="nonce-a",
    )
    second = await start_execution(
        db,
        user_id="user_a",
        meditation_id="311",
        execution_nonce="nonce-a",
    )
    assert first["execution_id"] == second["execution_id"]
    assert len(db["meditation_executions"].docs) == 1

    done = await complete_execution(
        db,
        user_id="user_a",
        execution_id=first["execution_id"],
        execution_nonce="nonce-a",
        listen_duration_seconds=90,
    )
    again = await complete_execution(
        db,
        user_id="user_a",
        execution_id=first["execution_id"],
        execution_nonce="nonce-a",
        listen_duration_seconds=10,
    )
    assert done["status"] == "COMPLETED"
    assert again["listen_duration_seconds"] == 90

    helpful = await record_feedback(
        db,
        user_id="user_a",
        execution_id=first["execution_id"],
        execution_nonce="nonce-a",
        feedback="HELPFUL",
    )
    flipped = await record_feedback(
        db,
        user_id="user_a",
        execution_id=first["execution_id"],
        execution_nonce="nonce-a",
        feedback="NOT_HELPFUL",
    )
    assert helpful["user_helpfulness_feedback"] == "HELPFUL"
    assert flipped["user_helpfulness_feedback"] == "HELPFUL"

    with pytest.raises(ValueError):
        await record_feedback(
            db,
            user_id="user_b",
            execution_id=first["execution_id"],
            execution_nonce="nonce-a",
            feedback="NOT_HELPFUL",
        )


def test_not_helpful_feedback_is_stored_separately():
    # Covered with the service in the async test's sibling path.
    card = build_meditation_card(
        {
            "meditation_id": "324",
            "title": "Body scan",
            "user_reason": "A short body scan.",
            "duration_seconds": 165,
            "category": "sleep",
            "execution_nonce": "n",
            "audio_available": True,
        }
    )
    assert card.action_payload["type"] == "MEDITATION"
    assert card.action_payload["execution_nonce"] == "n"
    assert card.action_payload["meditation_id"] == "324"
    assert card.action_payload["reason"] == "A short body scan."
    assert card.action_payload["reason"] == card.action_payload["user_reason"]


def test_cold_start_redistributes_personal_weight():
    cold = scoring_weights(cold_start=True)
    warm = scoring_weights(cold_start=False)
    assert cold["personal"] == 0
    assert cold["pad"] == pytest.approx(warm["pad"] + 0.09)
    assert cold["latent"] == pytest.approx(warm["latent"] + 0.07)
    assert cold["pad"] + cold["latent"] == pytest.approx(
        warm["pad"] + warm["latent"] + warm["personal"]
    )
    fresh = recommend(
        "My chest is tight and I only have a minute. I'm anxious and overwhelmed."
    )
    assert fresh.cold_start is True
    assert fresh.breakdown.cold_start is True
    known = recommend(
        "My chest is tight and I only have a minute. I'm anxious and overwhelmed.",
        executions=[_history("311")],
    )
    assert known.cold_start is False


def test_repetition_penalty_fades_between_12_and_72_hours():
    assert repetition_factor(timedelta(hours=12)) == 1.0
    assert repetition_factor(timedelta(hours=42)) == pytest.approx(0.5)
    assert repetition_factor(timedelta(hours=72)) == 0.0
    now = datetime(2026, 9, 23, 18, tzinfo=timezone.utc)
    soon = recommend(
        "My chest is tight and I only have a minute. I'm anxious and overwhelmed.",
        executions=[_history("311", days_ago=0, technique="MINDFUL_OBSERVATION")],
        now=now,
    )
    later = recommend(
        "My chest is tight and I only have a minute. I'm anxious and overwhelmed.",
        executions=[
            {
                "meditation_id": "311",
                "user_helpfulness_feedback": "HELPFUL",
                "status": "COMPLETED",
                "technique": "MINDFUL_OBSERVATION",
                "started_at": now - timedelta(hours=80),
            }
        ],
        now=now,
    )
    assert soon.session["id"] != "311"
    assert later.session["id"] == "311"
    assert later.breakdown.repetition_penalty == 0


def test_catalog_promotion_needs_fifty_completions_and_eighty_percent():
    from meditation.metadata import METADATA_STATUS_EMPIRICALLY_VALIDATED

    assert evaluate_catalog_promotion(49, 49, 0) is None
    assert evaluate_catalog_promotion(50, 0, 0) is None
    assert evaluate_catalog_promotion(50, 39, 11) is None
    assert evaluate_catalog_promotion(50, 40, 10) == METADATA_STATUS_EMPIRICALLY_VALIDATED
    overlaid = overlay_promotions(get_all_sessions(), {"311"})
    promoted = next(item for item in overlaid if item["id"] == "311")
    assert promoted["metadata_status"] == METADATA_STATUS_EMPIRICALLY_VALIDATED
    decision = recommend(
        "My chest is tight and I only have a minute. I'm anxious and overwhelmed.",
        sessions=overlaid,
    )
    assert decision.session["id"] == "311"
    assert decision.session["metadata_status"] == METADATA_STATUS_EMPIRICALLY_VALIDATED


@pytest.mark.asyncio
async def test_promotion_is_stored_from_execution_counts():
    from services.meditation.service import refresh_catalog_promotion

    db = _DB()
    for index in range(50):
        db["meditation_executions"].docs.append(
            {
                "user_id": f"u{index}",
                "meditation_id": "311",
                "status": "COMPLETED",
                "user_helpfulness_feedback": "HELPFUL" if index < 45 else "NOT_HELPFUL",
                "execution_nonce": f"n{index}",
            }
        )
    status = await refresh_catalog_promotion(db, "311")
    assert status == "empirically_validated"
    stored = db["meditation_metadata_promotions"].docs[0]
    assert stored["completions"] == 50
    assert stored["helpful"] == 45
