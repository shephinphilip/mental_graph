"""One meditation card per turn. Internal scores stay off the card."""

from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from schemas import ActionCard, CardType
from meditation.audio import get_audio_path


def _is_meditation(card: ActionCard) -> bool:
    return (card.action_payload or {}).get("type") == "MEDITATION"


def build_meditation_card(offer: Dict[str, Any]) -> ActionCard:
    """User-facing card. No PAD, probabilities, or APM fields."""
    session = offer.get("session") or {}
    meditation_id = str(offer.get("meditation_id") or session.get("id") or "")
    duration = offer.get("duration_seconds") or session.get("duration_seconds")
    minutes = max(1, int(round((duration or 60) / 60))) if duration else None
    reason = offer.get("reason") or offer.get("user_reason") or ""
    title = offer.get("title") or session.get("title") or "A short practice"
    subtitle_bits = []
    if minutes:
        subtitle_bits.append(f"{minutes} minutes")
    if reason:
        subtitle_bits.append(reason)
    return ActionCard(
        card_type=CardType.TOOL,
        card_id=f"card_meditation_{meditation_id}",
        title=title,
        subtitle=" · ".join(subtitle_bits) if subtitle_bits else "Optional practice",
        cta_label="Start",
        action_payload={
            "type": "MEDITATION",
            "meditation_id": meditation_id,
            "execution_nonce": offer.get("execution_nonce") or secrets.token_urlsafe(18),
            "category": offer.get("category") or session.get("category"),
            "duration_seconds": duration,
            "reason": reason,
            "user_reason": reason,
            "audio_available": bool(offer.get("audio_available")),
        },
    )


_PRACTICE_CARDS = {CardType.TOOL, CardType.HABIT, CardType.TASK, CardType.CONTENT}


def _one_practice_card(cards: List[ActionCard]) -> List[ActionCard]:
    """At most one habit, task, tool, or content card. Booking cards stay."""
    seen = False
    kept: List[ActionCard] = []
    for card in cards:
        if card.card_type in _PRACTICE_CARDS or _is_meditation(card):
            if seen:
                continue
            seen = True
        kept.append(card)
    return kept


def ensure_single_meditation_card(
    cards: List[ActionCard],
    offer: Optional[Dict[str, Any]],
    *,
    suppress: bool = False,
) -> List[ActionCard]:
    """
    Keep at most one meditation card, and only the ranked one.

    Model-invented meditation cards are dropped. A psychiatrist / crisis
    card on the same turn suppresses the practice.
    """
    kept = [card for card in cards if not _is_meditation(card)]
    if suppress or not offer or offer.get("decision") != "RECOMMEND_MEDITATION":
        return _one_practice_card(kept)
    if any(
        (card.action_payload or {}).get("type") in {"CRISIS_SUPPORT", "PSYCHIATRIST_REFERRAL"}
        or card.card_id in {"card_crisis_support_v1", "card_psychiatrist_v1"}
        for card in kept
    ):
        return [
            card
            for card in kept
            if card.card_type not in _PRACTICE_CARDS and not _is_meditation(card)
        ]
    protected = [card for card in kept if card.card_type not in _PRACTICE_CARDS]
    return protected + [build_meditation_card(offer)]


def user_visible_fields(card: Dict[str, Any]) -> Dict[str, Any]:
    payload = card.get("action_payload") or {}
    duration = payload.get("duration_seconds")
    minutes = max(1, int(round(duration / 60))) if duration else None
    return {
        "heading": "Recommended for right now",
        "title": card.get("title"),
        "minutes": minutes,
        "reason": payload.get("reason") or payload.get("user_reason") or card.get("subtitle"),
        "execution_nonce": payload.get("execution_nonce"),
        "category": payload.get("category"),
        "meditation_id": payload.get("meditation_id"),
        "audio_available": bool(payload.get("audio_available")),
    }


def audio_path_for_card(card: Dict[str, Any]) -> Optional[str]:
    payload = card.get("action_payload") or {}
    if payload.get("type") != "MEDITATION":
        return None
    path = get_audio_path(str(payload.get("meditation_id") or ""))
    return str(path) if path else None
