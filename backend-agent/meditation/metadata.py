"""
Reviewable meditation attributes.

This file is independent of the catalog in ``data.py``. Nothing here is a
clinical validation. Each entry is a provisional content reading of the
session title and description: what state it seems aimed at, how much
effort it asks for, and which technique it uses.

Duration and friction are not invented here. They are measured from the
audio file when the catalog is built. If a recording is several minutes
long, it is not labeled as a two-minute practice.
"""

from __future__ import annotations

from typing import Any, Dict

METADATA_STATUS_PROVISIONAL = "provisional"
METADATA_STATUS_UNREVIEWED = "unreviewed"
METADATA_STATUS_EMPIRICALLY_VALIDATED = "empirically_validated"
# Provisional stays eligible. Validated is the same content with enough
# completed listens and explicit helpfulness to leave the provisional label.
ELIGIBLE_METADATA_STATUSES = frozenset(
    {METADATA_STATUS_PROVISIONAL, METADATA_STATUS_EMPIRICALLY_VALIDATED}
)


def _provisional(**fields: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "metadata_status": METADATA_STATUS_PROVISIONAL,
        "metadata_basis": "title_and_description",
    }
    payload.update(fields)
    return payload


# Keys are existing session IDs. Sessions omitted here stay unreviewed and
# are not recommendation candidates.
SESSION_METADATA: Dict[str, Dict[str, Any]] = {
    "311": _provisional(
        pad_coordinates={
            "target_valence": 0.15,
            "target_arousal": -0.45,
            "target_dominance": 0.25,
        },
        target_latent_states=["ANXIETY_HIGH", "STRESS_HIGH", "OVERWHELM_HIGH", "PANIC_SPIRAL"],
        required_cognitive_load="VERY_LOW",
        technique="MINDFUL_OBSERVATION",
        tags=["stress", "anxiety", "grounding"],
        languages=["en"],
        user_reason=(
            "A short, low-effort practice that settles the body "
            "instead of asking you to concentrate hard."
        ),
    ),
    "312": _provisional(
        pad_coordinates={
            "target_valence": 0.2,
            "target_arousal": -0.35,
            "target_dominance": 0.3,
        },
        target_latent_states=["ANXIETY_HIGH", "STRESS_HIGH"],
        required_cognitive_load="MODERATE",
        technique="MINDFUL_OBSERVATION",
        tags=["anxiety", "longer sit"],
        languages=["en"],
        user_reason="A longer anxiety practice, only when there is room to stay with it.",
    ),
    "314": _provisional(
        pad_coordinates={
            "target_valence": 0.1,
            "target_arousal": -0.25,
            "target_dominance": 0.35,
        },
        target_latent_states=["ANXIETY_HIGH", "STRESS_HIGH", "OVERWHELM_HIGH"],
        required_cognitive_load="LOW",
        technique="LABELLING",
        tags=["stress", "labelling"],
        languages=["en"],
        user_reason="A light labelling practice for sorting thoughts without pushing them away.",
    ),
    "315": _provisional(
        pad_coordinates={
            "target_valence": 0.25,
            "target_arousal": -0.4,
            "target_dominance": 0.3,
        },
        target_latent_states=["ANXIETY_HIGH", "STRESS_HIGH", "OVERWHELM_HIGH"],
        required_cognitive_load="VERY_LOW",
        technique="PRESENT_MOMENT",
        tags=["stress", "grounding", "present"],
        languages=["en"],
        user_reason="A short return to the present moment, with very little to track.",
    ),
    "321": _provisional(
        pad_coordinates={
            "target_valence": 0.2,
            "target_arousal": -0.7,
            "target_dominance": 0.15,
        },
        target_latent_states=["SLEEP_PREPARATION"],
        required_cognitive_load="VERY_LOW",
        technique="MUSCLE_RELAXATION",
        tags=["sleep", "body", "evening"],
        languages=["en"],
        user_reason="A gentle wind-down that lets the body soften before sleep.",
    ),
    "322": _provisional(
        pad_coordinates={
            "target_valence": 0.15,
            "target_arousal": -0.65,
            "target_dominance": 0.15,
        },
        target_latent_states=["SLEEP_PREPARATION"],
        required_cognitive_load="VERY_LOW",
        technique="BREATH",
        tags=["sleep", "breath"],
        languages=["en"],
        user_reason="A quiet breathing practice for settling toward sleep.",
    ),
    "324": _provisional(
        pad_coordinates={
            "target_valence": 0.2,
            "target_arousal": -0.72,
            "target_dominance": 0.12,
        },
        target_latent_states=["SLEEP_PREPARATION"],
        required_cognitive_load="VERY_LOW",
        technique="BODY_SCAN",
        tags=["sleep", "body scan"],
        languages=["en"],
        user_reason="A short body scan meant for letting the day go before sleep.",
    ),
    "331": _provisional(
        pad_coordinates={
            "target_valence": 0.15,
            "target_arousal": 0.05,
            "target_dominance": 0.45,
        },
        target_latent_states=["FOCUS_RECOVERY", "COGNITIVE_FATIGUE"],
        required_cognitive_load="LOW",
        technique="FOCUS",
        tags=["focus", "attention", "work"],
        languages=["en"],
        user_reason="A short focus practice for when attention is scattered and the list feels loud.",
    ),
    "343": _provisional(
        pad_coordinates={
            "target_valence": 0.3,
            "target_arousal": -0.35,
            "target_dominance": 0.28,
        },
        target_latent_states=["CALM", "STRESS_HIGH"],
        required_cognitive_load="LOW",
        technique="BODY_SCAN",
        tags=["body scan"],
        languages=["en"],
        user_reason="A body scan you can stay with when there is a little time.",
    ),
    "347": _provisional(
        pad_coordinates={
            "target_valence": 0.35,
            "target_arousal": -0.4,
            "target_dominance": 0.32,
        },
        target_latent_states=["CALM"],
        required_cognitive_load="MODERATE",
        technique="BODY_SCAN",
        tags=["body scan", "longer sit"],
        languages=["en"],
        user_reason="A longer body scan, for a steadier moment when you want to stay with sensation.",
    ),
    "350": _provisional(
        pad_coordinates={
            "target_valence": 0.25,
            "target_arousal": -0.75,
            "target_dominance": 0.1,
        },
        target_latent_states=["SLEEP_PREPARATION"],
        required_cognitive_load="MODERATE",
        technique="BODY_SCAN",
        tags=["sleep", "body scan", "longer sit"],
        languages=["en"],
        user_reason="A longer sleep body scan, only when you are not already overloaded.",
    ),
    "356": _provisional(
        pad_coordinates={
            "target_valence": 0.45,
            "target_arousal": -0.15,
            "target_dominance": 0.2,
        },
        target_latent_states=["LOW_MOOD", "SOCIAL_WITHDRAWAL"],
        required_cognitive_load="VERY_LOW",
        technique="SELF_COMPASSION",
        tags=["self-compassion", "kindness"],
        languages=["en"],
        user_reason=(
            "A self-compassion practice, offered lightly — "
            "not because something is wrong with you."
        ),
    ),
    "359": _provisional(
        pad_coordinates={
            "target_valence": 0.5,
            "target_arousal": -0.1,
            "target_dominance": 0.25,
        },
        target_latent_states=["LOW_MOOD", "SOCIAL_WITHDRAWAL"],
        required_cognitive_load="LOW",
        technique="LOVING_KINDNESS",
        tags=["loving-kindness", "connection"],
        languages=["en"],
        user_reason="A loving-kindness practice aimed at warmth toward yourself and others.",
    ),
    "1201": _provisional(
        pad_coordinates={
            "target_valence": 0.3,
            "target_arousal": -0.25,
            "target_dominance": 0.25,
        },
        target_latent_states=["CALM", "STRESS_HIGH"],
        required_cognitive_load="VERY_LOW",
        technique="MINDFUL_OBSERVATION",
        tags=["hindi", "mindfulness"],
        languages=["hi"],
        user_reason="A Hindi mindfulness practice for a quieter, present-moment sit.",
    ),
    "1202": _provisional(
        pad_coordinates={
            "target_valence": 0.3,
            "target_arousal": -0.25,
            "target_dominance": 0.25,
        },
        target_latent_states=["CALM", "STRESS_HIGH"],
        required_cognitive_load="VERY_LOW",
        technique="MINDFUL_OBSERVATION",
        tags=["kannada", "mindfulness"],
        languages=["kn"],
        user_reason="A Kannada mindfulness practice for a quieter, present-moment sit.",
    ),
}


def friction_for_duration(duration_seconds: int | None) -> str | None:
    """
    MICRO < 2 min, LOW 2–5 min, MEDIUM 5–10 min, DEEP > 10 min.

    The 5-minute boundary belongs to MEDIUM (inclusive).
    """
    if duration_seconds is None or duration_seconds <= 0:
        return None
    if duration_seconds < 120:
        return "MICRO"
    if duration_seconds < 300:
        return "LOW"
    if duration_seconds <= 600:
        return "MEDIUM"
    return "DEEP"
