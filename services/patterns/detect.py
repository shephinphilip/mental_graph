"""Deterministic detectors — facts → candidate patterns (not diagnoses)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Dict, List, Optional

from config import get_settings
from schemas import PatternDomain, PatternType
from services.patterns.adapters import group_marks_by_subject, personal_baseline
from services.patterns.score import (
    classify_status,
    compute_confidence,
    strength_from_evidence,
)


def fingerprint(user_id: str, pattern_type: str, key: str) -> str:
    digest = sha256(f"{user_id}|{pattern_type}|{key}".encode("utf-8")).hexdigest()
    return digest[:32]


def detect_candidates(
    user_id: str, observations: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """
    Produce candidate pattern documents from normalized observations.

    A single co-occurrence yields OBSERVATION status only — never ESTABLISHED.
    """
    candidates: List[Dict[str, Any]] = []
    candidates.extend(_academic_decline(user_id, observations.get("academic") or []))
    candidates.extend(_mood_recurrence(user_id, observations.get("mood") or []))
    candidates.extend(_mood_change_point(user_id, observations.get("mood") or []))
    candidates.extend(
        _stress_academic_cross_domain(
            user_id,
            observations.get("mood") or [],
            observations.get("academic") or [],
            observations.get("conversation") or [],
        )
    )
    candidates.extend(
        _intervention_response(user_id, observations.get("apm") or [])
    )
    candidates.extend(
        _theme_recurrence(user_id, observations.get("conversation") or [])
    )
    candidates.extend(_sleep_patterns(user_id, observations))
    candidates.extend(_journal_patterns(user_id, observations))
    return candidates


def _academic_decline(user_id: str, academic: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    grouped = group_marks_by_subject(academic)
    for subject, rows in grouped.items():
        if len(rows) < 3:
            continue
        values = [float(r["value"]) for r in rows if r.get("value") is not None]
        if len(values) < 3:
            continue
        recent = values[-3:]
        earlier = values[:-3] or values[:1]
        if sum(recent) / len(recent) >= (sum(earlier) / len(earlier)) - 3:
            continue
        evidence = len(recent)
        key = f"academic_decline:{subject.lower()}"
        conf = compute_confidence(
            evidence_count=evidence,
            consistency=0.8,
            data_quality=0.9,
        )
        out.append(
            _candidate(
                user_id=user_id,
                pattern_type=PatternType.ACADEMIC,
                domains=[PatternDomain.ACADEMIC.value],
                key=key,
                description=(
                    f"{subject} marks have declined across the last three assessments "
                    f"relative to this user's earlier personal baseline. Tentative trend, "
                    f"not a diagnosis or fixed ability judgment."
                ),
                observations=[
                    {"feature": "subject", "condition": subject},
                    {"feature": "recent_avg", "condition": round(sum(recent) / len(recent), 1)},
                    {
                        "feature": "earlier_avg",
                        "condition": round(sum(earlier) / len(earlier), 1),
                    },
                ],
                evidence_count=evidence,
                confidence=conf,
                last_at=rows[-1].get("at"),
                source="marks",
            )
        )
    return out


def _mood_recurrence(user_id: str, moods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stressed = [m for m in moods if m.get("stressed") and m.get("at")]
    if len(stressed) < 2:
        return []
    by_weekday = Counter(m["at"].weekday() for m in stressed if m.get("at"))
    if not by_weekday:
        return []
    weekday, count = by_weekday.most_common(1)[0]
    if count < 2:
        return []
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    key = f"mood_recurrence:weekday:{weekday}"
    conf = compute_confidence(evidence_count=count, consistency=count / max(len(stressed), 1))
    return [
        _candidate(
            user_id=user_id,
            pattern_type=PatternType.RECURRENCE,
            domains=[PatternDomain.MOOD.value, PatternDomain.CONVERSATION.value],
            key=key,
            description=(
                f"Lower or stressed mood check-ins have clustered on {names[weekday]}s "
                f"for this user. Recurrence observation only — not proof of a clinical cycle."
            ),
            observations=[
                {"feature": "weekday", "condition": names[weekday]},
                {"feature": "stressed_mood_count", "condition": count},
            ],
            evidence_count=count,
            confidence=conf,
            last_at=max(m["at"] for m in stressed if m.get("at")),
            source="mood_logs",
        )
    ]


def _mood_change_point(user_id: str, moods: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    scored = [
        float(m["score"])
        for m in moods
        if m.get("score") is not None
    ]
    if len(scored) < 6:
        return []
    settings = get_settings()
    window = settings.PATTERN_BASELINE_WINDOW_DAYS
    baseline = personal_baseline(scored[3:], window=window)
    recent = scored[:3]
    if baseline is None:
        return []
    recent_avg = sum(recent) / len(recent)
    # Lower score = worse mood in many trackers; also handle inverted scales gently
    if abs(recent_avg - baseline) < 1.0:
        return []
    direction = "lower" if recent_avg < baseline else "higher"
    evidence = 3
    key = f"mood_change_point:{direction}"
    conf = compute_confidence(evidence_count=evidence, consistency=0.7, data_quality=0.75)
    return [
        _candidate(
            user_id=user_id,
            pattern_type=PatternType.CHANGE_POINT,
            domains=[PatternDomain.MOOD.value],
            key=key,
            description=(
                f"Recent mood scores look {direction} than this user's personal "
                f"baseline over the prior window. Change-point observation — "
                f"not a clinical conclusion."
            ),
            observations=[
                {"feature": "recent_avg_score", "condition": round(recent_avg, 2)},
                {"feature": "personal_baseline", "condition": round(baseline, 2)},
            ],
            evidence_count=evidence,
            confidence=conf,
            last_at=moods[0].get("at") if moods else None,
            source="mood_logs",
        )
    ]


def _stress_academic_cross_domain(
    user_id: str,
    moods: List[Dict[str, Any]],
    academic: List[Dict[str, Any]],
    insights: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    stress_days = sum(1 for m in moods if m.get("stressed"))
    theme_hits = 0
    for insight in insights:
        themes = set(insight.get("value") or [])
        emotions = set(insight.get("emotions") or [])
        if themes & {"exam_stress", "academic_pressure", "study_stress", "school"}:
            theme_hits += 1
        if emotions & {"anxious", "stressed", "overwhelmed"}:
            theme_hits += 1

    declining = False
    grouped = group_marks_by_subject(academic)
    for rows in grouped.values():
        vals = [float(r["value"]) for r in rows if r.get("value") is not None]
        if len(vals) >= 3 and vals[-1] < vals[0] - 5:
            declining = True
            break

    evidence = 0
    if stress_days >= 2:
        evidence += min(stress_days, 4)
    if theme_hits >= 2:
        evidence += min(theme_hits, 3)
    if declining:
        evidence += 2
    if evidence < 2:
        return []

    key = "cross:stress_academic"
    conf = compute_confidence(
        evidence_count=evidence,
        consistency=0.65 if declining else 0.5,
        data_quality=0.7,
    )
    return [
        _candidate(
            user_id=user_id,
            pattern_type=PatternType.CROSS_DOMAIN,
            domains=[
                PatternDomain.MOOD.value,
                PatternDomain.ACADEMIC.value,
                PatternDomain.CONVERSATION.value,
            ],
            key=key,
            description=(
                "Stressed mood check-ins and academic-pressure themes have repeatedly "
                "appeared alongside this user's school performance signals. "
                "Tentative co-occurrence — not proof that marks cause stress or vice versa."
            ),
            observations=[
                {"feature": "stressed_mood_days", "condition": stress_days},
                {"feature": "academic_theme_hits", "condition": theme_hits},
                {"feature": "marks_declining_any_subject", "condition": declining},
            ],
            evidence_count=evidence,
            confidence=conf,
            last_at=(moods[0].get("at") if moods else None),
            source="mood_logs+marks+user_insights",
        )
    ]


def _intervention_response(
    user_id: str, apm_events: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    by_tool: Dict[str, List[Dict[str, Any]]] = {}
    for ev in apm_events:
        tool = str(ev.get("intervention_id") or "unknown")
        by_tool.setdefault(tool, []).append(ev)

    out = []
    for tool, events in by_tool.items():
        helpful = sum(1 for e in events if e.get("helpful"))
        not_helpful = sum(1 for e in events if e.get("value") == "NOT_HELPFUL")
        if helpful + not_helpful < 2:
            continue
        evidence = helpful + not_helpful
        consistency = helpful / float(evidence) if evidence else 0.0
        conf = compute_confidence(
            evidence_count=evidence,
            contradiction_count=not_helpful,
            consistency=consistency,
            data_quality=0.95,
        )
        out.append(
            _candidate(
                user_id=user_id,
                pattern_type=PatternType.INTERVENTION_RESPONSE,
                domains=[PatternDomain.MEDITATION.value, PatternDomain.APM.value],
                key=f"intervention_response:{tool}",
                description=(
                    f"After using intervention '{tool}', this user has given "
                    f"{helpful} helpful and {not_helpful} not-helpful reports. "
                    f"Attributable feedback only — completion alone is not success."
                ),
                observations=[
                    {"feature": "intervention_id", "condition": tool},
                    {"feature": "helpful", "condition": helpful},
                    {"feature": "not_helpful", "condition": not_helpful},
                ],
                evidence_count=evidence,
                confidence=conf,
                contradiction_count=not_helpful,
                last_at=events[0].get("at"),
                source="apm_events",
            )
        )
    return out


def _theme_recurrence(
    user_id: str, insights: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    counter: Counter = Counter()
    last_at: Dict[str, Any] = {}
    for insight in insights:
        for theme in insight.get("value") or []:
            t = str(theme).lower().strip()
            if not t or t in {"greeting", "small_talk"}:
                continue
            counter[t] += 1
            if insight.get("at"):
                last_at[t] = insight["at"]
    out = []
    for theme, count in counter.most_common(5):
        if count < 2:
            continue
        key = f"theme_recurrence:{theme}"
        conf = compute_confidence(evidence_count=count, consistency=0.75)
        out.append(
            _candidate(
                user_id=user_id,
                pattern_type=PatternType.TEMPORAL,
                domains=[PatternDomain.CONVERSATION.value],
                key=key,
                description=(
                    f"The theme '{theme}' has recurred across multiple non-crisis "
                    f"conversations for this user. Recurring concern — not a diagnosis."
                ),
                observations=[{"feature": "theme", "condition": theme}],
                evidence_count=count,
                confidence=conf,
                last_at=last_at.get(theme),
                source="user_insights",
            )
        )
    return out


def _journal_patterns(
    user_id: str, observations: Dict[str, List[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    """Journal findings. Hypotheses and contradictions are not stored."""
    from journaling.patterns import findings

    entries = observations.get("journaling") or []
    academic = [
        {"date": _day(item.get("at") or item.get("date")), "value": item.get("value")}
        for item in (observations.get("academic") or [])
    ]
    found = findings(
        entries,
        sleep_rows=observations.get("sleep") or [],
        tasks=observations.get("tasks") or [],
        academic=academic,
        feedback=observations.get("meditation_feedback") or [],
    )
    out = []
    for item in found:
        if item.get("level") not in {"observation", "pattern"}:
            continue
        last = None
        if entries:
            last = entries[0].get("observed_at")
        out.append(
            _candidate(
                user_id=user_id,
                pattern_type=PatternType.BEHAVIORAL,
                domains=[PatternDomain.JOURNALING.value],
                key=item["key"],
                description=item["text"],
                observations=[{"level": item["level"], "source": "journal_entries"}],
                evidence_count=int(item.get("evidence") or 1),
                confidence=compute_confidence(
                    evidence_count=int(item.get("evidence") or 1),
                    contradiction_count=int(item.get("contradictions") or 0),
                    consistency=0.75,
                    data_quality=0.8,
                ),
                last_at=last if isinstance(last, datetime) else None,
                source="journal_entries",
                contradiction_count=int(item.get("contradictions") or 0),
            )
        )
    return out


def _day(value: Any) -> str:
    if hasattr(value, "date"):
        return value.date().isoformat()
    return str(value or "")[:10]


def _sleep_patterns(user_id: str, observations: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Turn sleep findings into pattern candidates. Hypotheses are not stored."""
    from sleep.patterns import findings

    rows = []
    for obs in observations.get("sleep") or []:
        rows.append(
            {
                "user_id": user_id,
                "date": obs.get("date"),
                "bedtime": obs.get("bedtime"),
                "wake_up_time": obs.get("wake_up_time"),
                "total_duration_minutes": obs.get("value"),
                "created_at": obs.get("observed_at"),
            }
        )
    moods = [
        {"date": _day(item.get("at")), "value": item.get("value")}
        for item in (observations.get("mood") or [])
    ]
    academic = [
        {"date": _day(item.get("at") or item.get("date")), "value": item.get("value")}
        for item in (observations.get("academic") or [])
    ]
    found = findings(
        rows,
        moods=moods,
        tasks=observations.get("tasks") or [],
        academic=academic,
        executions=observations.get("sleep_practices") or [],
    )
    out = []
    for item in found:
        if item.get("level") not in {"observation", "pattern"}:
            continue
        last = rows[0].get("created_at") if rows else None
        out.append(
            _candidate(
                user_id=user_id,
                pattern_type=PatternType.BEHAVIORAL,
                domains=[PatternDomain.SLEEP.value],
                key=item["key"],
                description=item["text"],
                observations=[{"level": item["level"], "source": "sleep_logs"}],
                evidence_count=int(item.get("evidence") or 1),
                confidence=compute_confidence(
                    evidence_count=int(item.get("evidence") or 1),
                    contradiction_count=int(item.get("contradictions") or 0),
                    consistency=0.75,
                    data_quality=0.8,
                ),
                last_at=last if isinstance(last, datetime) else None,
                source="sleep_logs",
                contradiction_count=int(item.get("contradictions") or 0),
            )
        )
    return out


def _candidate(
    *,
    user_id: str,
    pattern_type: PatternType,
    domains: List[str],
    key: str,
    description: str,
    observations: List[Dict[str, Any]],
    evidence_count: int,
    confidence: float,
    last_at: Optional[datetime],
    source: str,
    contradiction_count: int = 0,
) -> Dict[str, Any]:
    settings = get_settings()
    status = classify_status(evidence_count)
    return {
        "user_id": user_id,
        "fingerprint": fingerprint(user_id, pattern_type.value, key),
        "pattern_type": pattern_type.value,
        "domains": domains,
        "description": description,
        "observations": observations,
        "evidence_count": evidence_count,
        "confidence": confidence,
        "strength": strength_from_evidence(evidence_count, 0.7),
        "status": status.value,
        "decay_rate": settings.PATTERN_DECAY_PER_DAY,
        "last_observed_at": last_at or datetime.now(timezone.utc),
        "first_observed_at": last_at or datetime.now(timezone.utc),
        "contradiction_count": contradiction_count,
        "source_hint": source,
        "interpretation": (
            "Tentative relationship based on repeated personal observations. "
            "Not causation. Not a clinical diagnosis."
        ),
    }
