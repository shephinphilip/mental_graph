# API reference

Compatibility paths are listed first. Every handler is also mounted under
`/api/v1` with the same suffix (`POST /chat/send` → `POST /api/v1/chat/send`,
`POST /api/mood` → `POST /api/v1/mood`).

Authentication header for every AUTHENTICATED route:

```
Authorization: Bearer <access_token>
X-Request-ID: optional opaque hex
```

Error envelope (all failures). `detail` is kept for older clients:

```json
{
  "success": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Authentication required.",
    "request_id": "…"
  },
  "detail": "Authentication required."
}
```

Rate limits (process-local, configurable): login/signup, chat, stream, and
session report are tighter than the default. Health probes are not limited.

---

## POST /auth/login

Purpose: Email/password login.

Authentication: PUBLIC.

Request:

```json
{ "email": "ananya.rao@zenark.demo", "password": "Zenark@123" }
```

Success:

```json
{
  "user_id": "usr_…",
  "access_token": "…",
  "token_type": "bearer",
  "email": "ananya.rao@zenark.demo",
  "name": "Ananya",
  "student_class": null,
  "school": null,
  "preferred_language": "ENGLISH",
  "age": null,
  "chief_concern": null,
  "board": null,
  "personalization_consent": false
}
```

Error 401: invalid email or password. Password is never returned.

Idempotency: none. Related: `POST /auth/signup`.

---

## POST /auth/signup

Purpose: Create an account; same session payload as login.

Authentication: PUBLIC.

Request: `{ "email", "password", "name?" }`.

Success: 201 `LoginResponse`. Error 409 if the email already exists.

---

## POST /api/language  and  POST /api/v1/language

Purpose: Store `users.preferred_language` for the token owner.

Authentication: Bearer. Authorization: body `user_id` cannot retarget.

Request: `{ "language": "HINDI", "user_id": null }`.

Success: `{ "preferred_language", "preferred_language_updated_at" }`.

Error 400 invalid enum, 403 mismatch, 404 missing user.

---

## POST /api/memory/consent

Purpose: Toggle `users.personalization_consent`.

Request: `{ "enabled": true }`. Success: `{ "enabled": true }`.

---

## POST /api/memory/feedback

Purpose: Explicit APM HELPFUL / NOT_HELPFUL.

Request: `APMFeedbackRequest` (`edge_id`, `intervention_id`, `execution_nonce`,
`event_type`, optional `before_state` / `after_state`).

Success: `{ "recorded": true }`.

---

## DELETE /api/memory

Purpose: Delete derived memory only (APM, patterns, student facts,
meditation executions/offers). Messages and Graph RAG stay.

Success: deletion counts. Related: `POST /api/memory/consolidate`.

---

## POST /api/memory/consolidate

Purpose: Rebuild the profile summary from stored facts.

---

## POST /api/patterns/feedback

Purpose: Confirm or disagree with a detected pattern.

Request: `{ "pattern_id", "event_type": "CONFIRMED"|"DISAGREED", "note?" }`.

Error 404 if the pattern is missing.

---

## POST /consultation-evaluation/manual

Purpose: Run the existing consultation decision for self, or another user if staff.

Request: `{ "user_id": null }`.

---

## POST /consultation-evaluation/manual-override

Purpose: Staff override. Authentication: STAFF.

Request: `{ "user_id", "status", "reason" }` (`reason` ≥ 5 characters in the domain).

---

## POST /consultation-evaluation/batch

Purpose: Staff batch evaluation. Request: `{ "user_ids": ["…"] }`.

---

## GET /consultation-evaluation/status

Purpose: Signed-in user's latest status + unread notification count.

Self only. Signals stay internal.

---

## POST /chat/welcome

Purpose: Idempotent AI opening turn.

Authentication: Bearer + OWNER (`user_id` in body).

Request:

```json
{ "user_id": "usr_…", "session_id": "session_123" }
```

Success: `ChatMessageResponse` `{ session_id, reply, action_cards }`.

Idempotent: existing welcome is returned; a session with other messages
does not insert a new welcome.

External: Bedrock only when no welcome/messages exist.

---

## POST /chat/send

Purpose: Full LangGraph JSON turn.

Authentication: Bearer + OWNER.

Request:

```json
{
  "session_id": "session_123",
  "user_id": "usr_…",
  "message": "I've been feeling overwhelmed today."
}
```

Success:

```json
{
  "session_id": "session_123",
  "reply": "…",
  "action_cards": []
}
```

Error 401 / 403 as above. 500 returns the generic internal envelope.

Background: `run_background_extraction` after the response (in-process).

Streaming: no. Related: `POST /chat/stream`.

---

## POST /chat/stream

Purpose: Same decisions as `/chat/send`, Server-Sent Events.

Authentication: Bearer + OWNER. Same body as `/chat/send`.

Response: `text/event-stream`. Do not stitch two models after the first token
(existing streaming contract). Heartbeat / disconnect handling live in
`services/streaming.py`.

Rate limit: `RATE_LIMIT_STREAM_PER_MINUTE`.

---

## GET /chat/session/{user_id}/resume

Purpose: Last messages + dropped-session context.

Authentication: Bearer + OWNER (path `user_id`). Query: `session_id?`.

Response: `SessionResumeResponse`.

---

## Journal

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/journal/entry` | Create. Body: `JournalEntryRequest`. |
| GET | `/journal/recent-entries` | Previews only. |
| GET | `/journal/entry/{entry_id}` | Full entry. 404 if missing. |
| GET | `/journal/past-reflections` | Older previews. |
| GET | `/journal/calendar-data` | `{ days }` |
| GET | `/journal/favorites` | Favorited previews. |
| GET | `/journal/stats` | Counts from `journaling.service.stats`. |
| GET | `/journal/monthly-mindfulness` | Month rollup. |

Auth: Bearer, self. Body `user_id` cannot retarget a create.

---

## Sleep

| Method | Path | Body / query |
| --- | --- | --- |
| POST | `/api/sleep` | `SleepLogRequest` |
| GET | `/api/sleep/recent` | latest night or null |
| GET | `/api/sleep/history` | `?days=` clamped to `MAX_LIMIT` |

---

## Tracking

| Method | Path | Notes |
| --- | --- | --- |
| POST | `/api/mood` | `MoodLogRequest`. `client_event_id` idempotent. `crisis` if the note is flagged. |
| GET | `/api/mood/recent` | `?days=` clamped |
| GET | `/api/habits` | `?include_archived=` |
| POST | `/api/habits` | `HabitCreateRequest` |
| PATCH | `/api/habits/{habit_id}` | `HabitPatchRequest` |
| POST | `/api/habits/{habit_id}/check-in` | idempotent per day |
| POST | `/api/habits/streaks` | `{ "show_streaks": true }` |

---

## Tasks

| Method | Path |
| --- | --- |
| GET | `/api/report_card/tasks/{claimed_user_id}` |
| POST | `/api/report_card/tasks/complete` |
| POST | `/api/report_card/tasks/custom` |
| PATCH | `/api/report_card/tasks/custom/{claimed_user_id}/{task_id}` |
| POST | `/api/reports/tasks/accept` |

Owner checks use `tasks.identity` / `owns_claimed_id`.

---

## POST /api/session/report

Purpose: Post-conversation reading + at most one meditation rank.

Request: `{ "session_id": "session_123" }`.

Auth: Bearer (self). External: Bedrock. Rate limit: report bucket.

---

## Meditation

| Method | Path | Body |
| --- | --- | --- |
| POST | `/api/meditation/preview` | `{ "message"? }` |
| POST | `/api/meditation/start` | `MeditationStartRequest` |
| POST | `/api/meditation/complete` | `MeditationCompleteRequest` |
| POST | `/api/meditation/feedback` | `MeditationFeedbackRequest` |

Audio files are not served by these routes.

---

## GET /health

Compatibility probe: `{ "status": "healthy", "service", "version" }`. PUBLIC.

## GET /health/live

`{ "status": "live" }`. Process only.

## GET /health/ready

`{ "status": "ready" }` or 503 `{ "status": "not_ready", "reason" }`. Mongo ping.
Does not expose URIs or credentials.

Machine-readable schema: `docs/api/openapi.yaml`.
