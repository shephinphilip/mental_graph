# Mood check-ins and habits

`tracking/` is the only writer of `mood_logs` and `habit_events`. Both
collections were already read — by the chat prompt (`services/context.py`), the
pattern engine (`services/patterns/adapters.py`), and meditation ranking
(`services/meditation/service.py`) — but nothing ever wrote them, so every user
looked like someone who had never logged anything. This closes that gap without
introducing a second store.

## What a check-in is

One tap. `mood` is the only required field. `score` (1–10), `note`, and
`input_format` are optional, so an emoji grid, a colour slider, a voice
transcript, and a text box all post to the same route.

```
POST /api/mood            {"mood": "anxious", "score": 3, "note": "before the mock"}
GET  /api/mood/recent     ?days=7
```

The row carries two timestamps on purpose. `logged_at` is what the prompt reader
sorts on; `created_at` is what the pattern engine windows on. A backfilled entry
sets both to the moment being described, so a late sync lands on the right day
rather than bunching up at the time it uploaded. `synced_at` records when it
actually arrived.

**Offline retries.** A client may send `client_event_id`. The second send returns
the first row with `duplicate: true` instead of logging again, which is enforced
by a partial unique index rather than by client discipline.

**Crisis wording in a note.** The entry is stored, `crisis_flagged` is set, the
response returns `crisis: true` so the surface can offer helplines, and the
mood adapter skips it. A hard moment is never quietly turned into training
signal for pattern detection.

## Habits

```
GET    /api/habits
POST   /api/habits                      {"title": "Morning walk", "frequency": "daily"}
PATCH  /api/habits/{habit_id}           {"status": "paused"}
POST   /api/habits/{habit_id}/check-in  {}
POST   /api/habits/streaks              {"show_streaks": true}
```

Habits are created by the person, not assigned. `frequency` is stored lowercase
because the prompt reads the word out as written. A check-in is idempotent: the
same day twice changes nothing and returns `already_logged: true`.

### Streaks forgive a miss

`current_streak()` walks back day by day and allows up to
`HABIT_GRACE_MISSES_PER_WEEK` (default 1) missed days inside any trailing window
of seven examined days. One gap keeps the run going; two close together end it.
A weekly habit counts consecutive ISO weeks with at least one completion, and
the current week is never counted against someone while it is still open.

### Streaks are opt-in, including for the AI

The true value lives in `streak_internal`. The `streak` field the prompt reads is
only mirrored while the user has turned streaks on, and `POST /api/habits/streaks`
rewrites the mirror across their habits both ways. Someone who has opted out is
not shown a number and is not reminded of one by the chatbot either.

## Ownership

Every function takes the authenticated id and runs `identity_keys` /
`owns_claimed_id` from `tasks.identity`. A `user_id` in a body is checked against
the token's aliases and is never the thing that selects the target row.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `HABIT_GRACE_MISSES_PER_WEEK` | 1 | Missed days forgiven inside any trailing week |
| `HABIT_MAX_ACTIVE` | 20 | Ceiling on simultaneously active habits |
| `MOOD_BACKFILL_MAX_DAYS` | 30 | How far back an offline sync may date an entry |
| `MOOD_LOG_LOOKBACK_DAYS` | 7 | Window the prompt reader summarises |

## What this deliberately does not do

- Completing a habit is not reported to adaptive memory as a helpful outcome.
  Only an explicit HELPFUL / NOT_HELPFUL on a response is.
- Nothing here nudges, reminds, or scores the person. `reminder_time` is stored
  for a future scheduler; no notification is sent today.
- Custom symptom trackers and wearable imports from the product spec are not
  built yet. They belong in this package when they are.
