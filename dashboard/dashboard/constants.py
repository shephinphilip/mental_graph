"""Collection names and hard caps. Caps keep one school from loading unbounded history."""

from __future__ import annotations

USERS = "users"
MARKS = "marks"
MOODS = "mood_logs"
SLEEP = "sleep_logs"
PATTERNS = "user_patterns"
RISK_TURNS = "user_risk_turns"
MEDITATIONS = "meditation_executions"
PROFILES = "student_psychological_profiles"

INTERVENTIONS = "dashboard_interventions"
NOTIFICATIONS = "dashboard_notifications"
REPORTS = "dashboard_reports"
SETTINGS = "dashboard_settings"
AUDIT = "dashboard_audit"
TEACHER_ACTIONS = "dashboard_teacher_actions"

MAX_USERS = 5000
MAX_MARKS = 20000
MAX_SIGNALS = 20000

CACHE_TTL_SECONDS = 45
CACHE_MAX_ENTRIES = 64

QUADRANT_STUDENT_CAP = 50
ATTENTION_CAP = 15
TOP_PERFORMER_CAP = 5
TEACHER_ACTION_CAP = 20

# student_profile.py: latest >= 75 is a strength, latest < 45 is a struggle.
STRENGTH_PERCENTAGE = 75.0
STRUGGLE_PERCENTAGE = 45.0
# student_profile.py academic-pressure score treats latest < 50 as the heavy band.
PRESSURE_PERCENTAGE = 50.0
