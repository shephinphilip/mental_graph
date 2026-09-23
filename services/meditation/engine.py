"""
Personalized meditation ranking.

The same broad feeling does not map to one fixed session. A candidate has
to survive a hard filter, then a documented weighted score that mixes the
current estimate, content attributes, attributable outcomes, and repetition.

Engagement is not evidence. Card views, clicks, and time-in-app never enter
the score. Only explicit HELPFUL / NOT_HELPFUL outcomes do, plus APM
RECOVERED_BY edges that already carry explicit success or failure counts.

Weights live in config so they can be reviewed. They exist to keep state
match and effort-fit ahead of personal history, and to keep history from
overriding a clearly different current state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from config import get_settings
from meditation.data import get_all_sessions
from meditation.metadata import ELIGIBLE_METADATA_STATUSES, friction_for_duration
from services.apm import contains_crisis_signal, temporal_bucket

LATENT_STATES = (
    "ANXIETY_HIGH",
    "PANIC_SPIRAL",
    "OVERWHELM_HIGH",
    "COGNITIVE_FATIGUE",
    "LOW_MOOD",
    "SOCIAL_WITHDRAWAL",
    "ANGER_HIGH",
    "STRESS_HIGH",
    "CALM",
    "FOCUS_RECOVERY",
    "SLEEP_PREPARATION",
)

# Phrase lists are cues, not diagnoses. Several states may be active at once.
_LEXICON: Dict[str, tuple] = {
    "PANIC_SPIRAL": ("panic", "heart racing", "can't breathe", "cant breathe"),
    "ANXIETY_HIGH": ("anxious", "anxiety", "worried", "on edge", "chest is tight", "nervous"),
    "OVERWHELM_HIGH": ("overwhelmed", "too much", "can't cope", "cant cope", "only a minute", "no bandwidth"),
    "COGNITIVE_FATIGUE": ("exhausted", "burnt out", "burned out", "brain fog", "wiped", "can't think"),
    "LOW_MOOD": ("low mood", "feeling low", "feel low", "empty", "hopeless", "numb", "depressed"),
    "SOCIAL_WITHDRAWAL": ("don't want to see", "pulling away", "isolat", "withdraw", "no one"),
    "ANGER_HIGH": ("furious", "so angry", "rage", "pissed off"),
    "STRESS_HIGH": ("stressed", "stress", "pressure", "workload"),
    "CALM": ("fairly steady", "feel steady", "pretty calm", "i feel calm", "have time to sit"),
    "FOCUS_RECOVERY": ("can't focus", "cant focus", "get my focus", "attention", "backlog", "scattered"),
    "SLEEP_PREPARATION": ("can't sleep", "cant sleep", "wind down", "before bed", "to sleep", "fall asleep"),
}

# Valence, arousal, dominance anchors in [-1, 1]. Used only inside ranking.
_ANCHORS = {
    "ANXIETY_HIGH": (-0.55, 0.72, -0.35),
    "PANIC_SPIRAL": (-0.8, 0.9, -0.55),
    "OVERWHELM_HIGH": (-0.5, 0.7, -0.5),
    "COGNITIVE_FATIGUE": (-0.35, 0.15, -0.3),
    "LOW_MOOD": (-0.7, -0.25, -0.4),
    "SOCIAL_WITHDRAWAL": (-0.5, -0.15, -0.35),
    "ANGER_HIGH": (-0.4, 0.75, 0.15),
    "STRESS_HIGH": (-0.4, 0.55, -0.2),
    "CALM": (0.45, -0.35, 0.35),
    "FOCUS_RECOVERY": (0.05, 0.1, -0.05),
    "SLEEP_PREPARATION": (-0.05, -0.2, 0.05),
}

_LANGUAGE_CODES = {
    "english": "en",
    "en": "en",
    "hindi": "hi",
    "hi": "hi",
    "kannada": "kn",
    "kn": "kn",
}

_LOAD_RANK = {"VERY_LOW": 0, "LOW": 1, "MODERATE": 2}
_MAX_PAD_DISTANCE = math.sqrt(3 * (2**2))  # corners of the [-1, 1] cube


@dataclass
class LatentEstimate:
    state: str
    probability: float


@dataclass
class StateEstimate:
    valence: float
    arousal: float
    dominance: float
    confidence: float
    latent: List[LatentEstimate]
    allowed_friction: set
    preferred_friction: set
    allowed_cognitive_load: set
    preferred_language: str
    time_bucket: str
    top_state: Optional[str] = None

    def as_public_debug(self) -> Dict[str, Any]:
        return {
            "valence": round(self.valence, 3),
            "arousal": round(self.arousal, 3),
            "dominance": round(self.dominance, 3),
            "confidence": round(self.confidence, 3),
            "latent": [
                {"state": item.state, "probability": round(item.probability, 3)}
                for item in self.latent
            ],
            "allowed_friction": sorted(self.allowed_friction),
            "allowed_cognitive_load": sorted(self.allowed_cognitive_load),
            "time_bucket": self.time_bucket,
        }


@dataclass
class ScoreBreakdown:
    meditation_id: str
    pad_score: float
    state_match: float
    friction_match: float
    cognitive_load_match: float
    personal_success: float
    recency: float
    repetition_penalty: float
    uncertainty_penalty: float
    language_bonus: float
    final_score: float
    title: str = ""
    cold_start: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "meditation_id": self.meditation_id,
            "title": self.title,
            "pad_score": round(self.pad_score, 3),
            "state_match": round(self.state_match, 3),
            "friction_match": round(self.friction_match, 3),
            "cognitive_load_match": round(self.cognitive_load_match, 3),
            "personal_success": round(self.personal_success, 3),
            "recency": round(self.recency, 3),
            "repetition_penalty": round(self.repetition_penalty, 3),
            "uncertainty_penalty": round(self.uncertainty_penalty, 3),
            "language_bonus": round(self.language_bonus, 3),
            "final_score": round(self.final_score, 3),
            "cold_start": self.cold_start,
        }


@dataclass
class MeditationDecision:
    decision: str
    reason: str
    user_reason: str = ""
    session: Optional[Dict[str, Any]] = None
    breakdown: Optional[ScoreBreakdown] = None
    ranked: List[ScoreBreakdown] = field(default_factory=list)
    estimate: Optional[StateEstimate] = None
    withheld_reason: str = ""
    cold_start: bool = False

    def prompt_block(self) -> str:
        if self.decision != "RECOMMEND_MEDITATION" or not self.session:
            return (
                "MEDITATION THIS TURN: NO_MEDITATION. "
                "Do not suggest a meditation and do not invent a practice card. "
                "Do not mention scores, coordinates, or internal state labels."
            )
        minutes = _minutes(self.session.get("duration_seconds"))
        return (
            "MEDITATION THIS TURN: RECOMMEND_MEDITATION. "
            "At most this one practice. The UI will show the card and audio. "
            "You may acknowledge it in one warm, optional sentence using the reason below. "
            "If the moment is wrong for a practice, do not force it in the wording, "
            "but do not propose a different meditation. "
            "Do not quote numbers, PAD, confidence, probabilities, or latent-state labels. "
            "Do not emit an ACTION_CARD for it.\n"
            f"Title: {self.session.get('title')}\n"
            f"About {minutes} minutes.\n"
            f"Say it naturally as: {self.user_reason}"
        )


def pad_distance(
    user_valence: float,
    user_arousal: float,
    user_dominance: float,
    target_valence: float,
    target_arousal: float,
    target_dominance: float,
) -> float:
    return math.sqrt(
        (target_valence - user_valence) ** 2
        + (target_arousal - user_arousal) ** 2
        + (target_dominance - user_dominance) ** 2
    )


def pad_match_score(distance: float) -> float:
    if _MAX_PAD_DISTANCE <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - (distance / _MAX_PAD_DISTANCE)))


def _minutes(duration_seconds: Optional[int]) -> int:
    if not duration_seconds:
        return 0
    return max(1, int(round(duration_seconds / 60)))


def _language_code(value: Optional[str]) -> str:
    from services.language_preferences import language_rank_code

    mapped = _LANGUAGE_CODES.get((value or "en").strip().casefold())
    if mapped:
        return mapped
    return language_rank_code(value)


def _phrase_probs(text: str) -> Dict[str, float]:
    lowered = (text or "").casefold()
    found: Dict[str, float] = {}
    if not lowered.strip():
        return found
    for state, phrases in _LEXICON.items():
        hits = sum(1 for phrase in phrases if phrase in lowered)
        if hits:
            found[state] = min(0.92, 0.5 + 0.14 * (hits - 1))
    if "night" in lowered or "evening" in lowered or "late" in lowered:
        found["SLEEP_PREPARATION"] = max(found.get("SLEEP_PREPARATION", 0.0), 0.42)
    return found


def _merge_probs(message: str, context_text: str) -> Dict[str, float]:
    primary = _phrase_probs(message)
    context = _phrase_probs(context_text)
    merged: Dict[str, float] = {}
    for state in set(primary) | set(context):
        # Context can surface a state the message left vague, but it cannot
        # outvote a clear statement in the current turn.
        merged[state] = max(primary.get(state, 0.0), context.get(state, 0.0) * 0.45)
    return {key: value for key, value in merged.items() if value >= 0.2}


def _capacity(top: Optional[str], arousal: float) -> tuple:
    high_activation = top in {
        "PANIC_SPIRAL",
        "ANXIETY_HIGH",
        "OVERWHELM_HIGH",
    } or arousal >= 0.55
    if high_activation:
        return {"MICRO", "LOW"}, {"MICRO"}, {"VERY_LOW"}
    if top in {"STRESS_HIGH", "COGNITIVE_FATIGUE", "FOCUS_RECOVERY", "ANGER_HIGH"}:
        return {"MICRO", "LOW"}, {"MICRO", "LOW"}, {"VERY_LOW", "LOW"}
    if top in {"CALM"} or (top in {"SLEEP_PREPARATION", "LOW_MOOD"} and arousal < 0.35):
        return (
            {"MICRO", "LOW", "MEDIUM", "DEEP"},
            {"LOW", "MEDIUM", "DEEP"},
            {"VERY_LOW", "LOW", "MODERATE"},
        )
    return {"MICRO", "LOW", "MEDIUM"}, {"LOW"}, {"VERY_LOW", "LOW"}


def estimate_state(
    message: str,
    *,
    preferred_language: str = "en",
    mood_text: str = "",
    sleep_text: str = "",
    academic_text: str = "",
    attendance_text: str = "",
    task_text: str = "",
    journal_text: str = "",
    now: Optional[datetime] = None,
) -> StateEstimate:
    now = now or datetime.now(timezone.utc)
    context = "\n".join(
        part
        for part in (mood_text, sleep_text, academic_text, attendance_text, task_text, journal_text)
        if part
    )
    probs = _merge_probs(message, context)
    bucket = temporal_bucket(now)
    if bucket in {"EVENING", "LATE_NIGHT"} and probs.get("SLEEP_PREPARATION"):
        probs["SLEEP_PREPARATION"] = min(0.92, probs["SLEEP_PREPARATION"] + 0.08)

    ranked = sorted(probs.items(), key=lambda item: item[1], reverse=True)
    latent = [LatentEstimate(state, prob) for state, prob in ranked]
    top = latent[0].state if latent else None
    top_prob = latent[0].probability if latent else 0.0

    if latent:
        weight = sum(item.probability for item in latent) or 1.0
        valence = sum(_ANCHORS[item.state][0] * item.probability for item in latent) / weight
        arousal = sum(_ANCHORS[item.state][1] * item.probability for item in latent) / weight
        dominance = sum(_ANCHORS[item.state][2] * item.probability for item in latent) / weight
    else:
        valence = arousal = dominance = 0.0

    confidence = min(0.92, 0.2 + 0.75 * top_prob) if top else 0.15
    allowed, preferred, loads = _capacity(top, arousal)
    return StateEstimate(
        valence=valence,
        arousal=arousal,
        dominance=dominance,
        confidence=confidence,
        latent=latent,
        allowed_friction=allowed,
        preferred_friction=preferred,
        allowed_cognitive_load=loads,
        preferred_language=_language_code(preferred_language),
        time_bucket=bucket,
        top_state=top,
    )


def _compatible_state(estimate: StateEstimate, targets: Sequence[str]) -> bool:
    if not estimate.latent:
        return False
    target_set = set(targets)
    top = estimate.latent[0]
    if top.probability >= 0.45 and top.state not in target_set:
        return False
    return any(item.state in target_set and item.probability >= 0.35 for item in estimate.latent)


def _state_match(estimate: StateEstimate, targets: Sequence[str]) -> float:
    target_set = set(targets)
    matched = [item.probability for item in estimate.latent if item.state in target_set]
    if not matched:
        return 0.0
    if estimate.top_state in target_set:
        return 1.0
    return max(0.45, min(0.85, max(matched)))


def _personal_success(
    meditation_id: str,
    executions: Sequence[Dict[str, Any]],
    apm_success: Dict[str, float],
) -> float:
    helpful = 0
    unhelpful = 0
    for row in executions:
        if str(row.get("meditation_id")) != str(meditation_id):
            continue
        feedback = (row.get("user_helpfulness_feedback") or "").upper()
        if feedback == "HELPFUL":
            helpful += 1
        elif feedback == "NOT_HELPFUL":
            unhelpful += 1
    if helpful + unhelpful == 0:
        return float(apm_success.get(str(meditation_id), 0.0) or 0.0)
    direct = helpful / (helpful + unhelpful)
    apm_value = float(apm_success.get(str(meditation_id), 0.0) or 0.0)
    # The same outcome may be stored twice. Do not add the two signals.
    return max(direct, apm_value)


def repetition_factor(age: timedelta) -> float:
    """
    1.0 for the first 12 hours, then a straight line to 0 at 72 hours.

    A practice finished Monday is not still blocked on Friday.
    """
    settings = get_settings()
    hours = max(0.0, age.total_seconds() / 3600.0)
    full_until = settings.MEDITATION_REPETITION_FULL_HOURS
    zero_at = settings.MEDITATION_REPETITION_ZERO_HOURS
    if hours <= full_until:
        return 1.0
    if hours >= zero_at or zero_at <= full_until:
        return 0.0
    return (zero_at - hours) / (zero_at - full_until)


def _has_attributable_history(
    executions: Sequence[Dict[str, Any]],
    apm_success: Dict[str, float],
) -> bool:
    for row in executions:
        feedback = (row.get("user_helpfulness_feedback") or "").upper()
        if feedback in {"HELPFUL", "NOT_HELPFUL"}:
            return True
    return any(float(value or 0) > 0 for value in apm_success.values())


def scoring_weights(*, cold_start: bool) -> Dict[str, float]:
    """
    Cold start moves the unused personal-success weight onto state match.

    The added 0.09 and 0.07 equal the 0.16 personal weight, so the scale
    of the other terms does not shrink just because the user is new.
    """
    settings = get_settings()
    pad = settings.MEDITATION_WEIGHT_PAD
    latent = settings.MEDITATION_WEIGHT_LATENT
    personal = settings.MEDITATION_WEIGHT_PERSONAL
    if cold_start:
        pad += settings.MEDITATION_COLD_START_PAD
        latent += settings.MEDITATION_COLD_START_LATENT
        personal = 0.0
    return {
        "pad": pad,
        "latent": latent,
        "friction": settings.MEDITATION_WEIGHT_FRICTION,
        "cognitive": settings.MEDITATION_WEIGHT_COGNITIVE,
        "personal": personal,
        "recency": settings.MEDITATION_WEIGHT_RECENCY,
    }


def evaluate_catalog_promotion(
    completions: int,
    helpful: int,
    not_helpful: int,
) -> Optional[str]:
    """
    Promote a provisional session once enough people have finished it
    and explicit feedback is at least 80% helpful.

    Completions with no helpfulness vote do not count as positive or negative.
    """
    from meditation.metadata import METADATA_STATUS_EMPIRICALLY_VALIDATED

    settings = get_settings()
    if completions < settings.MEDITATION_VALIDATION_MIN_COMPLETIONS:
        return None
    labeled = helpful + not_helpful
    if labeled <= 0:
        return None
    if (helpful / labeled) >= settings.MEDITATION_VALIDATION_MIN_HELPFUL_RATIO:
        return METADATA_STATUS_EMPIRICALLY_VALIDATED
    return None


def _repetition_penalty(
    meditation_id: str,
    technique: Optional[str],
    executions: Sequence[Dict[str, Any]],
    now: datetime,
) -> float:
    settings = get_settings()
    penalty = 0.0
    full = settings.MEDITATION_REPETITION_PENALTY
    for row in executions:
        started = row.get("started_at")
        if not isinstance(started, datetime):
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age = now - started
        if age.total_seconds() < 0:
            age = timedelta(0)
        factor = repetition_factor(age)
        if factor <= 0:
            continue
        same = str(row.get("meditation_id")) == str(meditation_id)
        same_technique = bool(technique) and row.get("technique") == technique and not same
        if same:
            penalty = max(penalty, full * factor)
        elif same_technique:
            penalty = max(penalty, full * 0.25 * factor)
    return penalty


def _recency_fit(targets: Sequence[str], bucket: str) -> float:
    if "SLEEP_PREPARATION" in targets:
        return 1.0 if bucket in {"EVENING", "LATE_NIGHT"} else 0.35
    if "FOCUS_RECOVERY" in targets:
        return 1.0 if bucket in {"MORNING", "AFTERNOON"} else 0.55
    return 0.6


def _passes_hard_filter(session: Dict[str, Any], estimate: StateEstimate) -> bool:
    if session.get("metadata_status") not in ELIGIBLE_METADATA_STATUSES:
        return False
    if not session.get("has_audio"):
        return False
    friction = session.get("friction_level") or friction_for_duration(session.get("duration_seconds"))
    if friction not in estimate.allowed_friction:
        return False
    load = session.get("required_cognitive_load")
    if load not in estimate.allowed_cognitive_load:
        return False
    if not session.get("duration_seconds"):
        return False
    if not _compatible_state(estimate, session.get("target_latent_states") or []):
        return False
    languages = session.get("languages") or []
    if session.get("category") == "regional" and estimate.preferred_language not in languages:
        return False
    return True


def rank_sessions(
    estimate: StateEstimate,
    *,
    executions: Sequence[Dict[str, Any]] = (),
    apm_success: Optional[Dict[str, float]] = None,
    now: Optional[datetime] = None,
    sessions: Optional[Iterable[Dict[str, Any]]] = None,
    sleep_support: Optional[Dict[str, Any]] = None,
) -> List[ScoreBreakdown]:
    settings = get_settings()
    apm_success = apm_success or {}
    now = now or datetime.now(timezone.utc)
    cold_start = not _has_attributable_history(executions, apm_success)
    weights = scoring_weights(cold_start=cold_start)
    ranked: List[ScoreBreakdown] = []
    catalog = list(sessions) if sessions is not None else get_all_sessions()

    for session in catalog:
        if not _passes_hard_filter(session, estimate):
            continue
        pad = session.get("pad_coordinates") or {}
        distance = pad_distance(
            estimate.valence,
            estimate.arousal,
            estimate.dominance,
            float(pad.get("target_valence", 0.0)),
            float(pad.get("target_arousal", 0.0)),
            float(pad.get("target_dominance", 0.0)),
        )
        pad_score = pad_match_score(distance)
        state_match = _state_match(estimate, session.get("target_latent_states") or [])
        friction = session.get("friction_level")
        friction_match = 1.0 if friction in estimate.preferred_friction else 0.7
        cognitive_match = 1.0
        personal = _personal_success(session["id"], executions, apm_success)
        recency = _recency_fit(session.get("target_latent_states") or [], estimate.time_bucket)
        languages = session.get("languages") or []
        language_bonus = (
            settings.MEDITATION_LANGUAGE_BONUS
            if estimate.preferred_language in languages
            else 0.0
        )
        repetition = _repetition_penalty(
            session["id"], session.get("technique"), executions, now
        )
        uncertainty = (1.0 - estimate.confidence) * settings.MEDITATION_UNCERTAINTY_PENALTY
        sleep_bonus = 0.0
        support = sleep_support or {}
        targets = session.get("target_latent_states") or []
        if support.get("prefer_sleep") and "SLEEP_PREPARATION" in targets:
            sleep_bonus += 0.04
        if support.get("prefer_low_effort") and session.get("required_cognitive_load") == "VERY_LOW":
            if session.get("friction_level") in {"MICRO", "LOW"}:
                sleep_bonus += 0.03
        final = (
            weights["pad"] * pad_score
            + weights["latent"] * state_match
            + weights["friction"] * friction_match
            + weights["cognitive"] * cognitive_match
            + weights["personal"] * personal
            + weights["recency"] * recency
            + language_bonus
            + sleep_bonus
            - repetition
            - uncertainty
        )
        ranked.append(
            ScoreBreakdown(
                meditation_id=session["id"],
                title=session.get("title") or "",
                pad_score=pad_score,
                state_match=state_match,
                friction_match=friction_match,
                cognitive_load_match=cognitive_match,
                personal_success=personal,
                recency=recency,
                repetition_penalty=repetition,
                uncertainty_penalty=uncertainty,
                language_bonus=language_bonus,
                final_score=final,
                cold_start=cold_start,
            )
        )
    ranked.sort(key=lambda item: item.final_score, reverse=True)
    return ranked


def estimate_from_report(
    *,
    valence: float,
    arousal: float,
    dominance: float,
    confidence: float,
    latent_states: Sequence[Dict[str, Any]],
    preferred_language: str = "en",
    patterns: Sequence[Dict[str, Any]] = (),
    now: Optional[datetime] = None,
) -> StateEstimate:
    """
    Build the ranking state from a report, not from a keyword scan.

    PAD, latent labels, and confidence come from the report model.
    Longitudinal patterns may add a secondary label, but they cannot
    outrank the report's top state.
    """
    now = now or datetime.now(timezone.utc)
    cleaned: List[LatentEstimate] = []
    seen = set()
    for item in latent_states or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("state") or "").strip().upper()
        if label not in LATENT_STATES or label in seen:
            continue
        try:
            probability = float(item.get("probability"))
        except (TypeError, ValueError):
            continue
        probability = max(0.0, min(1.0, probability))
        if probability < 0.2:
            continue
        seen.add(label)
        cleaned.append(LatentEstimate(label, probability))
    cleaned.sort(key=lambda item: item.probability, reverse=True)

    top = cleaned[0].state if cleaned else None
    top_prob = cleaned[0].probability if cleaned else 0.0
    ceiling = max(0.2, top_prob - 0.05) if cleaned else 0.35
    extras: Dict[str, float] = {}
    domain_to_state = {
        "sleep": "SLEEP_PREPARATION",
        "academic": "FOCUS_RECOVERY",
        "tasks": "COGNITIVE_FATIGUE",
        "journaling": "LOW_MOOD",
    }
    for pattern in patterns or []:
        bump = min(0.2, float(pattern.get("confidence") or 0.4) * 0.25)
        for domain in pattern.get("domains") or []:
            label = domain_to_state.get(str(domain))
            if not label or label == top:
                continue
            extras[label] = min(ceiling, max(extras.get(label, 0.0), bump))
    for label, probability in extras.items():
        if probability < 0.2 or any(item.state == label for item in cleaned):
            continue
        cleaned.append(LatentEstimate(label, probability))

    valence = max(-1.0, min(1.0, float(valence)))
    arousal = max(-1.0, min(1.0, float(arousal)))
    dominance = max(-1.0, min(1.0, float(dominance)))
    confidence = max(0.0, min(1.0, float(confidence)))
    allowed, preferred, loads = _capacity(top, arousal)
    return StateEstimate(
        valence=valence,
        arousal=arousal,
        dominance=dominance,
        confidence=confidence,
        latent=cleaned,
        allowed_friction=allowed,
        preferred_friction=preferred,
        allowed_cognitive_load=loads,
        preferred_language=_language_code(preferred_language),
        time_bucket=temporal_bucket(now),
        top_state=top,
    )


def recommend_from_estimate(
    estimate: StateEstimate,
    *,
    executions: Sequence[Dict[str, Any]] = (),
    apm_success: Optional[Dict[str, float]] = None,
    now: Optional[datetime] = None,
    sessions: Optional[Iterable[Dict[str, Any]]] = None,
    crisis: bool = False,
    sleep_support: Optional[Dict[str, Any]] = None,
) -> MeditationDecision:
    """Rank one practice from an already estimated session state."""
    if crisis:
        return MeditationDecision(
            "NO_MEDITATION",
            "",
            estimate=estimate,
            withheld_reason="crisis",
        )
    settings = get_settings()
    cold_start = not _has_attributable_history(executions, apm_success or {})
    if estimate.confidence < settings.MEDITATION_MIN_CONFIDENCE or not estimate.latent:
        return MeditationDecision(
            "NO_MEDITATION",
            "",
            estimate=estimate,
            withheld_reason="low_confidence",
            cold_start=cold_start,
        )
    catalog = list(sessions) if sessions is not None else get_all_sessions()
    ranked = rank_sessions(
        estimate,
        executions=executions,
        apm_success=apm_success,
        now=now,
        sessions=catalog,
        sleep_support=sleep_support,
    )
    if not ranked or ranked[0].final_score < settings.MEDITATION_MIN_SCORE:
        return MeditationDecision(
            "NO_MEDITATION",
            "",
            estimate=estimate,
            ranked=ranked,
            withheld_reason="no_candidate",
            cold_start=cold_start,
        )
    winner = ranked[0]
    session = next(item for item in catalog if item["id"] == winner.meditation_id)
    user_reason = session.get("user_reason") or (
        "A short, optional practice that looks like a gentle fit for this moment."
    )
    return MeditationDecision(
        "RECOMMEND_MEDITATION",
        user_reason,
        user_reason=user_reason,
        session=session,
        breakdown=winner,
        ranked=ranked[:5],
        estimate=estimate,
        cold_start=cold_start,
    )


def recommend(
    message: str,
    *,
    preferred_language: str = "en",
    mood_text: str = "",
    sleep_text: str = "",
    academic_text: str = "",
    attendance_text: str = "",
    task_text: str = "",
    journal_text: str = "",
    executions: Sequence[Dict[str, Any]] = (),
    apm_success: Optional[Dict[str, float]] = None,
    now: Optional[datetime] = None,
    opening_turn: bool = False,
    sessions: Optional[Iterable[Dict[str, Any]]] = None,
) -> MeditationDecision:
    """
    Return NO_MEDITATION or a single top session.

    Crisis language never produces a practice. Weak estimates do not either.
    Global popularity is not an input.
    """
    if opening_turn:
        return MeditationDecision("NO_MEDITATION", "", withheld_reason="opening_turn")
    if contains_crisis_signal(message or ""):
        return MeditationDecision("NO_MEDITATION", "", withheld_reason="crisis")

    estimate = estimate_state(
        message,
        preferred_language=preferred_language,
        mood_text=mood_text,
        sleep_text=sleep_text,
        academic_text=academic_text,
        attendance_text=attendance_text,
        task_text=task_text,
        journal_text=journal_text,
        now=now,
    )
    return recommend_from_estimate(
        estimate,
        executions=executions,
        apm_success=apm_success,
        now=now,
        sessions=sessions,
    )
