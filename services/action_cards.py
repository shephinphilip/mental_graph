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
from typing import List, Tuple

from schemas import ActionCard

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
