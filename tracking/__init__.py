"""Mood check-ins and habits.

Sole writer of `mood_logs` and `habit_events`. Both collections were already
read by the prompt context and the pattern engine; nothing wrote them.

Logging is the person's own record. It is never treated as a success signal
for adaptive memory, and a missed day is not a failure.
"""

from tracking.habits import (
    check_in,
    create_habit,
    current_streak,
    list_habits,
    set_streak_visibility,
    update_habit,
)
from tracking.mood import log_mood, recent_moods

__all__ = [
    "check_in",
    "create_habit",
    "current_streak",
    "list_habits",
    "log_mood",
    "recent_moods",
    "set_streak_visibility",
    "update_habit",
]
