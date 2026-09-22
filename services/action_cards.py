"""
services/action_cards.py — Action Card Parser
==============================================

Responsible for extracting structured ``ActionCard`` objects from the raw
text output of the LLM.

The LLM is instructed (via the system prompt) to append one or more action
card blocks to the end of its response using the following delimiter syntax::

    <<<ACTION_CARD
    {
      "card_type": "HABIT_CARD",
      "title":     "5-Minute Morning Journal",
      "subtitle":  "Build a daily reflection habit",
      "action_payload": {"habit_type": "journaling", "duration_mins": 5}
    }
    ACTION_CARD>>>

This module:
1. Uses a compiled regex to find all such blocks in the raw text.
2. Validates each JSON body against the ``ActionCard`` Pydantic schema.
3. Returns the cleaned conversational text (markup removed) and the
   list of validated ``ActionCard`` objects.

Design note
-----------
Malformed card blocks (invalid JSON or schema violations) are **skipped**
rather than raising exceptions.  This ensures the user always receives a
conversational reply even if the LLM produces a malformed card.
"""

import json
import logging
import re
import secrets
from typing import List, Optional, Tuple

from schemas import ActionCard, CardType

PSYCHIATRIST_CARD_ID = "card_psychiatrist_v1"
CRISIS_CARD_ID = "card_crisis_support_v1"

CRISIS_FAST_TRACK_REPLY = (
    "If you are in distress or having thoughts of self-harm, "
    "please know that you are not alone. Immediate support is available: "
    "Tele-MANAS 14416, Vandrevala +91 9999 666 555, "
    "KIRAN 1800-599-0019, AASRA +91 9820466726."
)


def build_crisis_support_card() -> ActionCard:
    """Immediate emergency-path card — not the 3-turn psychiatrist referral."""
    return ActionCard(
        card_type=CardType.BOOKING,
        card_id=CRISIS_CARD_ID,
        title="Connect to Professional Crisis Support",
        subtitle="Speak to a trained counselor immediately",
        cta_label="Get help now",
        action_payload={
            "type": "CRISIS_SUPPORT",
            "flow": "crisis_helpline_call",
            "card_id": CRISIS_CARD_ID,
            "execution_nonce": secrets.token_urlsafe(18),
        },
    )


def build_psychiatrist_referral_card(
    *,
    pattern_id: Optional[str] = None,
    trigger_reason: str = "",
) -> ActionCard:
    """Gentle Professional Care Hub card — never alarmist."""
    nonce = secrets.token_urlsafe(18)
    return ActionCard(
        card_type=CardType.BOOKING,
        card_id=PSYCHIATRIST_CARD_ID,
        title="Speak with a Licensed Psychiatrist",
        subtitle="In-app video or text sessions available in under 3 minutes",
        cta_label="Explore Care Options",
        action_payload={
            "type": "PSYCHIATRIST_REFERRAL",
            "flow": "psychiatrist_referral",
            "card_id": PSYCHIATRIST_CARD_ID,
            "execution_nonce": nonce,
            "pattern_id": pattern_id,
            "trigger_reason": trigger_reason,
        },
    )


def ensure_psychiatrist_card(
    cards: List[ActionCard],
    *,
    attach: bool,
    pattern_id: Optional[str] = None,
    trigger_reason: str = "",
) -> List[ActionCard]:
    """Append the referral card once if the window requested it."""
    if not attach:
        return cards
    already = any(
        (card.card_id == PSYCHIATRIST_CARD_ID)
        or (card.action_payload or {}).get("type") == "PSYCHIATRIST_REFERRAL"
        or card.card_type == CardType.BOOKING
        for card in cards
    )
    if already:
        return cards
    return list(cards) + [
        build_psychiatrist_referral_card(
            pattern_id=pattern_id, trigger_reason=trigger_reason
        )
    ]

logger = logging.getLogger(__name__)

# ── Compiled Regex Pattern ────────────────────────────────────────────────────
# Matches the full <<<ACTION_CARD { ... } ACTION_CARD>>> block.
# Flags:
#   re.DOTALL — allows the JSON body to span multiple lines (``\n`` matches ``.``)
# The capture group ``(\{.*?\})`` extracts only the JSON object body.
_ACTION_CARD_PATTERN = re.compile(
    r"<<<ACTION_CARD\s*(\{.*?\})\s*ACTION_CARD>>>",
    re.DOTALL,
)


def parse_action_cards(raw_output: str) -> Tuple[str, List[ActionCard]]:
    """
    Parse and validate action card blocks embedded in raw LLM output.

    Iterates over all ``<<<ACTION_CARD ... ACTION_CARD>>>`` delimiter blocks
    found in ``raw_output``, attempts to deserialise each JSON body into an
    ``ActionCard`` Pydantic model, and collects the valid results.  Blocks
    that fail JSON parsing or schema validation are silently skipped (with
    a ``WARNING`` log entry) to guarantee the caller always receives a
    usable reply string.

    Parameters
    ----------
    raw_output : str
        The full text string returned by the LLM, potentially containing
        one or more action card blocks at the end of the conversational text.

    Returns
    -------
    tuple[str, list[ActionCard]]
        A 2-tuple of:
        - ``clean_reply`` : The conversational text with all card markup
          stripped out and surrounding whitespace trimmed.  Safe to display
          directly to the user.
        - ``cards`` : A list of validated ``ActionCard`` instances.  May be
          empty if the LLM emitted no cards or all cards were malformed.

    Raises
    ------
    None
        This function never raises.  All errors are caught internally and
        logged at WARNING level.

    Examples
    --------
    ::

        raw = "Great idea! Here is a habit.\\n<<<ACTION_CARD\\n{...}\\nACTION_CARD>>>"
        reply, cards = parse_action_cards(raw)
        # reply == "Great idea! Here is a habit."
        # cards == [ActionCard(card_type=..., title=..., ...)]
    """
    cards: List[ActionCard] = []

    # Iterate over every regex match in the raw output
    for match in _ACTION_CARD_PATTERN.finditer(raw_output):
        json_str = match.group(1)  # The captured JSON body (between the delimiters)
        try:
            # Step 1: Parse the raw JSON string into a Python dict
            payload = json.loads(json_str)
            # Step 2: Validate the dict against the ActionCard Pydantic schema
            card = ActionCard(**payload)
            cards.append(card)

        except json.JSONDecodeError as exc:
            # The LLM produced a block that is not valid JSON (e.g. trailing comma,
            # unquoted key).  Log the first 120 chars to aid debugging.
            logger.warning(
                "Skipping action card block with invalid JSON (first 120 chars): %s — error: %s",
                json_str[:120],
                exc,
            )
        except (ValueError, TypeError) as exc:
            # The JSON is valid but does not conform to the ActionCard schema
            # (e.g. missing required field, invalid card_type enum value).
            logger.warning(
                "Skipping action card block that failed schema validation "
                "(first 120 chars): %s — error: %s",
                json_str[:120],
                exc,
            )

    # Remove all card delimiter blocks from the raw output, then strip
    # surrounding whitespace so the clean reply is ready to display.
    clean_reply = _ACTION_CARD_PATTERN.sub("", raw_output).strip()

    if cards:
        logger.info(
            "Parsed %d valid action card(s) from LLM output.",
            len(cards),
        )

    return clean_reply, cards


def attach_apm_execution_metadata(
    cards: List[ActionCard], user_id: str
) -> List[ActionCard]:
    """
    Add an opaque execution nonce only to APM-backed cards.

    The feedback endpoint still verifies edge ownership; the nonce merely makes
    retries idempotent and is not an authorization credential.
    """
    for card in cards:
        payload = card.action_payload
        if payload.get("edge_id") and payload.get("intervention_id"):
            payload["execution_nonce"] = secrets.token_urlsafe(18)
    return cards
