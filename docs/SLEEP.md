# Sleep in Zenark context

Self-reported nights stay in `sleep_logs`. Chat, the pattern engine, session reports, and meditation ranking read that collection. They do not copy it into Graph RAG, adaptive psychological memory, or a second sleep store.

## Schema

`sleep_logs` documents:

| Field | Meaning |
| --- | --- |
| `_id` | Mongo ObjectId |
| `user_id` | Chat identity. May be the token subject or the account email |
| `bedtime` | Free-text time, such as `23:00` or `11:00 PM` |
| `wake_up_time` | Free-text time |
| `total_duration_minutes` | Stored duration. Derived from the two times only when the client does not send one |
| `date` | `YYYY-MM-DD`. The night the sleep belongs to. A bedtime before 6:00 AM is stored as the previous calendar date |
| `created_at` | Server UTC timestamp |

There is no sleep-quality score, wearable stream, or nap log.

## Night date and duration

`date` is the sleep cycle, not the morning the person happens to be logging.

A bedtime of `1:30 AM` sent with `2026-09-23` is stored as `2026-09-22`. Evening bedtimes stay on the date that was sent. Send the calendar date of the log. The server applies this shift once.

When `total_duration_minutes` is omitted, duration comes from the two clock times. If wake time is earlier on the clock than bedtime, the span crosses midnight. `11:00 PM` to `7:15 AM` is 8h 15m.

## Readers

`get_recent_sleep(user_id)` returns the newest document by `created_at`. It does not filter by date.

`get_sleep_history(user_id, days=7)` returns every document with `created_at` inside that window, newest first, with no product limit.

Both queries use only aliases of the authenticated user: token subject, stored `user_id`, email, and the user document id. A `user_id` in a POST body or query string never widens that set. Writing or reading another person's nights returns 403 or an empty result.

`POST /api/sleep`, `GET /api/sleep/recent`, and `GET /api/sleep/history` all require the bearer token.

## What the model sees

The shared context builder (`fetch_user_context`) adds a compact block for both `/chat/send` and `/chat/stream`. The default window is 7 days (`SLEEP_CONTEXT_DAYS`).

```
RECENT SLEEP
Last night:
- Bedtime: …
- Wake time: …
- Duration: 7h 15m

Recent history:
- 2026-09-22 → 7h 15m
```

Invalid rows are left out: missing user or `created_at`, unreadable or impossible duration, a bad or future date. One row is kept per date, the latest `created_at`. A stored duration is used as-is. Missing values are not filled in. Mongo ids are not sent.

The block stays in the prompt even when the current message does not mention sleep, so a later turn about concentration can still see recent nights. The instruction is to mention sleep only when the message is about energy, focus, mood, stress, or rest, and never to call sleep a cause.

If the read fails, the block is `No sleep data available` and the reply still generates.

## Patterns

Detection runs on the existing background pattern pass, after consent, and never on a crisis turn. It compares the person with their own recent nights.

| Level | Example |
| --- | --- |
| Fact | A logged duration in the context block |
| Observation | Latest night is below this person's own recent average |
| Pattern | Shorter nights have repeatedly coincided with heavier mood check-ins, pending tasks, lower marks, or an irregular bedtime |
| Hypothesis | It may be worth asking whether they see a connection |

A single overlap is not stored as a pattern. Equal overlap with both heavier and lighter moods is treated as contradictory and dropped. Hypotheses are prompt language, not stored conclusions. Stored sleep patterns use the same decay and retrieval rules as other patterns. Crisis turns do not add evidence.

## Reports and meditation

`POST /api/session/report` sends the transcript, the sleep block, and any retrieved sleep patterns to the report model. The conversation still drives valence, arousal, dominance, and latent state. A short night does not override a calm conversation.

Meditation ranking can receive a small supporting hint after that state exists:

- shorter-than-usual sleep plus fatigue or overload can slightly favor a short, low-effort practice
- a sleep-preparation reading can slightly favor a sleep-oriented practice

Short sleep alone does not select a sleep meditation. The usual confidence, friction, language, repetition, and safety gates still apply.

## Retention

`sleep_logs` are tracker entries. Deleting adaptive memory removes derived `user_patterns`, not the nights the person logged. Pattern text is regenerated from the logs on the next consented detection pass.

## Indexes

- `{ user_id: 1, created_at: -1 }` for the recent and history readers
- `{ user_id: 1, date: -1 }` for a night keyed by the logged date

## Failure

A sleep read error does not fail the chat turn or the session report. Context becomes `No sleep data available`, and ranking continues without the sleep hint.
