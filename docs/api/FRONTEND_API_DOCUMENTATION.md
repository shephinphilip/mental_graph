# Zenark Frontend API Documentation

Source of truth: the running FastAPI application. Every URL below is
registered today. Sample payloads match the Pydantic models and the
dicts the handlers return. Success bodies do **not** wrap in
`{ "success": true }` unless shown — only errors use that envelope.

There are **no separate marks or attendance CRUD APIs**. The school dashboard under `/api/v1/dashboard` reads those collections for the signed-in school. Dashboard success bodies use `{ "success": true, "data", "meta" }`. See `docs/dashboard-api.md`.

## Base URL

Development:
http://localhost:8000

Staging:
<configured staging URL>

Production:
<configured production URL>

Use `/api/v1/...` for new work.

## Authentication

Most routes require:

```http
Authorization: Bearer <ACCESS_TOKEN>
```

Get the token from `POST /api/v1/auth/login` or `POST /api/v1/auth/signup`.
The default token lifetime is 12 hours (`AUTH_TOKEN_TTL_SECONDS=43200`)
unless the server is configured otherwise.

**Uses the authenticated user's identity.** A `user_id` in the path,
query, or body is only checked against the token. It does not select
another person's data. Send the signed-in `user_id` when the schema
requires it (chat, welcome, resume, some task paths).

Public (no token): login, signup, health probes.

Staff-only: `POST /api/v1/consultation-evaluation/manual-override` and
`POST /api/v1/consultation-evaluation/batch`.

## Common Headers

```http
Content-Type: application/json
Authorization: Bearer <ACCESS_TOKEN>
X-Request-ID: <optional opaque hex, max 64 alphanumeric chars>
```

`X-Request-ID` is optional. The server always returns one. If you send
a value that is not alphanumeric or is longer than 64 characters, the
server generates a new one.

## Error Format

Validation failures are **400**, not 422. The handler also echoes
`detail` for older clients.

```json
{
  "success": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Bearer token required",
    "request_id": "a28f57fc36194b50a4249822939991bb"
  },
  "detail": "Bearer token required"
}
```

| HTTP | `error.code` | Typical `message` |
|---|---|---|
| 400 | INVALID_REQUEST | Route-specific, or `Invalid request.` on schema failure |
| 401 | UNAUTHORIZED | `Bearer token required`, invalid token text, or `Invalid email or password` |
| 403 | FORBIDDEN | `User identity mismatch` or a domain permission string |
| 404 | NOT_FOUND | Route-specific |
| 409 | CONFLICT | Signup: email already exists |
| 429 | RATE_LIMITED | `Too many requests.` |
| 500 | INTERNAL_ERROR | `An unexpected error occurred.` |
| 502 | PROVIDER_ERROR | Speech provider failed (standalone STT) |
| 503 | NOT_READY | Ready probe only |

## Dates

| Kind | Format |
|---|---|
| Calendar date | `YYYY-MM-DD` (UTC day for tasks) |
| Date-time | ISO 8601 with offset, e.g. `2026-09-26T09:12:46.651874+00:00` |
| Clock (sleep) | Free text the server parses (`22:30`, `10:30 PM`) |
| Habit reminder | `HH:MM` 24-hour |
| Journal month | `YYYY-MM` |

`days` query params are clamped to **1–90** (`MAX_LIMIT`).

## Enums

**Language** (`POST /api/v1/language`): `ENGLISH`, `HINDI`, `HINGLISH`,
`TELUGU`, `TAMIL`, `MALAYALAM`, `KANNADA`, `BENGALI`, `GUJARATI`,
`PUNJABI`, `ODIA`, `URDU`.

**Mood (journal / tracking):** free string. Tracking clamps an optional
`score` to 1–10. Tracking `input_format` if sent: `EMOJI`, `SLIDER`,
`VOICE`, `TEXT` (anything else becomes `TEXT`).

**Habit `frequency`:** `daily`, `weekly`.

**Habit `status`:** `active`, `paused`, `archived`.

**APM `event_type`:** `STARTED`, `COMPLETED`, `HELPFUL`, `NOT_HELPFUL`.

**Pattern `event_type`:** `CONFIRM`, `DISAGREE`, `NOT_RELATED`,
`HELPFUL`, `NOT_HELPFUL`, `DISMISS`, `STARTED`.

**Meditation `feedback`:** `HELPFUL`, `NOT_HELPFUL`, `DISMISSED`.

**Meditation execution `status`:** `STARTED`, `COMPLETED`.

**Consultation `status`:** `REFERRED`, `MONITORING`, `NOT_NEEDED`
(status endpoint may also return `NOT_EVALUATED`).

**Action `card_type`:** `TOOL_CARD`, `HABIT_CARD`, `TASK_CARD`,
`BOOKING_CARD`, `CONTENT_CARD`.

**Report `task_persistence`:** `proposed`, `saved`, `existing`,
`skipped`, `failed`.

## Action cards

```json
{
  "card_type": "TOOL_CARD",
  "title": "A short practice",
  "subtitle": "5 minutes · Optional practice",
  "card_id": "card_meditation_med_abc",
  "cta_label": "Start",
  "action_payload": {
    "type": "MEDITATION",
    "meditation_id": "med_abc",
    "execution_nonce": "nonce_value",
    "category": "breath",
    "duration_seconds": 300,
    "reason": "A short reset",
    "user_reason": "A short reset",
    "audio_available": true
  }
}
```

`audio_available` is a boolean. There is **no audio file URL** on any
HTTP response. Do not expect a public media path from this API.

## Pagination

None of the list APIs take `page`, `cursor`, or `limit` query params.
Journal list endpoints return a server-side cap (recent/favorites:
50 previews; calendar/stats scan up to 500 rows). Mood recent defaults
to 7 days, max 90. Sleep history defaults to 7 days, max 90. Chat
resume returns the last 10 messages.

## Rate limits (defaults)

| Route family | Per minute |
|---|---|
| login / signup | 20 |
| chat send / welcome | 60 |
| chat stream | 30 |
| session report | 10 |
| voice STT | 20 |
| voice WebSocket session start | 10 |
| everything else | 300 |
| `/health*` | not limited |

Limits are per process. Health probes are skipped.

## API List

See [API_INVENTORY.md](API_INVENTORY.md). **49** unique operations
(47 HTTP + the voice WebSocket), each mounted at `/api/v1/...` (Active)
and a compatibility path.

---

# Authentication APIs

## POST /api/v1/auth/login

### Purpose
Authenticate by email. Password is never returned.

### Authentication
Not required.

### Request

Headers:

```http
Content-Type: application/json
```

Body:

```json
{
  "email": "user@example.com",
  "password": "ExamplePassword"
}
```

### Parameters

| Field | Type | Required | Description |
|---|---|---|---|
| email | string | Yes | Account email |
| password | string | Yes | Account password |

### Success Response — 200

```json
{
  "user_id": "usr_ab12cd34",
  "access_token": "eyJ...",
  "token_type": "bearer",
  "email": "user@example.com",
  "name": "Ananya",
  "student_class": "12",
  "school": "Example School",
  "preferred_language": "ENGLISH",
  "age": 17,
  "chief_concern": null,
  "board": "CBSE",
  "personalization_consent": false
}
```

### Response Fields

| Field | Type | Description |
|---|---|---|
| user_id | string | Authenticated identity |
| access_token | string | Bearer token |
| token_type | string | Always `bearer` |
| email | string or null | Email |
| name | string or null | Display name |
| student_class | string or null | Class / grade |
| school | string or null | School |
| preferred_language | string or null | Stored language enum |
| age | integer or null | Age if stored |
| chief_concern | string or null | If stored |
| board | string or null | School board |
| personalization_consent | boolean | Memory consent |

### Errors

#### 401

```json
{
  "success": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Invalid email or password",
    "request_id": "a28f57fc36194b50a4249822939991bb"
  },
  "detail": "Invalid email or password"
}
```

#### 400

Schema failure (`email` / `password` missing).

### Frontend Notes

Store `access_token` and `user_id`. There is no refresh endpoint.

**Legacy:** `POST /auth/login`

---

## POST /api/v1/auth/signup

### Purpose
Create an account. Returns the same session payload as login.

### Authentication
Not required.

### Request

Headers:

```http
Content-Type: application/json
```

Body:

```json
{
  "email": "user@example.com",
  "password": "ExamplePassword",
  "name": "Ananya"
}
```

`name` may be omitted.

### Parameters

| Field | Type | Required | Description |
|---|---|---|---|
| email | string | Yes | New email |
| password | string | Yes | New password |
| name | string | No | Display name |

### Success Response — 201

Same shape as login (`LoginResponse`).

### Errors

#### 409

Email already exists.

#### 400

Other validation from registration.

### Frontend Notes

**Legacy:** `POST /auth/signup`

---

# User / Profile

No dedicated profile GET/PATCH exists. Use the login/signup payload.

---

# Chat APIs

Chat bodies require `user_id`. It **must equal** the token subject or
the server returns 403 `User identity mismatch`.

## POST /api/v1/chat/welcome

### Purpose
AI opening turn for a new session. Persists only the assistant message.
Idempotent: if a welcome already exists, that reply is returned. If the
session already has other messages, no new welcome is inserted.

### Authentication
Bearer token required. Uses the authenticated user's identity.

### Request

Headers:

```http
Authorization: Bearer <ACCESS_TOKEN>
Content-Type: application/json
```

Body:

```json
{
  "user_id": "usr_ab12cd34",
  "session_id": "session_123"
}
```

### Parameters

| Field | Type | Required | Description |
|---|---|---|---|
| user_id | string | Yes | Must match the token |
| session_id | string | Yes | Conversation id |

### Success Response — 200

```json
{
  "session_id": "session_123",
  "reply": "How are things landing today?",
  "action_cards": []
}
```

### Response Fields

| Field | Type | Description |
|---|---|---|
| session_id | string | Echo of the session |
| reply | string | Assistant text (empty if an existing session has no assistant line) |
| action_cards | array | Parsed cards; often empty on welcome |

### Errors

#### 401 / 403

Standard auth / identity mismatch.

#### 500

`An unexpected error occurred.`

### Frontend Notes

Call once when opening a new session. Do not treat a second call as a
new greeting.

**Legacy:** `POST /chat/welcome`

---

## POST /api/v1/chat/send

### Purpose
Full turn. JSON reply after the model finishes.

### Authentication
Bearer token required. Uses the authenticated user's identity.

### Request

Headers:

```http
Authorization: Bearer <ACCESS_TOKEN>
Content-Type: application/json
```

Body:

```json
{
  "user_id": "usr_ab12cd34",
  "session_id": "session_123",
  "message": "I've been feeling overwhelmed today."
}
```

### Parameters

| Field | Type | Required | Description |
|---|---|---|---|
| user_id | string | Yes | Must match the token |
| session_id | string | Yes | Conversation id |
| message | string | Yes | User text |

### Success Response — 200

```json
{
  "session_id": "session_123",
  "reply": "That sounds like a lot to carry today.",
  "action_cards": []
}
```

### Response Fields

Same as welcome. `action_cards` items use the Action cards shape above.

### Errors

401, 403, 429, 500 as above.

### Frontend Notes

Prefer stream if you want tokens. Do not send both send and stream for
the same turn unless you intend two generations.

**Frontend usability improvement recommended:** `user_id` is redundant
with the Bearer token but the schema still requires it.

**Legacy:** `POST /chat/send`

---

## GET /api/v1/chat/session/{user_id}/resume

### Purpose
Hydrate the last session (last 10 messages).

### Authentication
Bearer token required. Path `user_id` must match the token.

### Request

Headers:

```http
Authorization: Bearer <ACCESS_TOKEN>
```

Path parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| user_id | string | Yes | Must match the token |

Query parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| session_id | string | No | Specific session; omitted uses the latest |

### Success Response — 200

```json
{
  "user_id": "usr_ab12cd34",
  "session_id": "session_123",
  "is_resumed": true,
  "last_message_timestamp": "2026-09-26T09:12:46+00:00",
  "dropped_session_context": "Resuming after last AI response: '…'",
  "recent_messages": [
    {
      "role": "user",
      "content": "I've been feeling overwhelmed today.",
      "timestamp": "2026-09-26T09:10:00+00:00"
    },
    {
      "role": "assistant",
      "content": "That sounds like a lot to carry today.",
      "timestamp": "2026-09-26T09:10:08+00:00"
    }
  ],
  "active_emotional_state": null
}
```

### Response Fields

| Field | Type | Description |
|---|---|---|
| user_id | string | Owner |
| session_id | string | Resolved session |
| is_resumed | boolean | `true` on success |
| last_message_timestamp | string or null | ISO time of last message |
| dropped_session_context | string or null | Short “where they left off” line |
| recent_messages | array | Oldest first; `role`, `content`, `timestamp` |
| active_emotional_state | string or null | Last stored label if present |

### Errors

401, 403, 500.

### Frontend Notes

**Legacy:** `GET /chat/session/{user_id}/resume`

---

# Streaming APIs

## POST /api/v1/chat/stream

### Purpose
Same authorization, context, language, and safety path as `/chat/send`.
Response is Server-Sent Events.

### Authentication
Bearer token required. Body `user_id` must match the token.

### Request

Headers:

```http
Authorization: Bearer <ACCESS_TOKEN>
Content-Type: application/json
Accept: text/event-stream
```

Body: same as `/chat/send`.

Response `Content-Type`: `text/event-stream`.

Extra response headers set by the server: `Cache-Control: no-cache`,
`Connection: keep-alive`, `X-Accel-Buffering: no`.

### SSE events (actual names)

Each event is:

```text
event: <name>
data: <json>

```

#### `token`

```text
event: token
data: {"token":"Hello"}
```

On an idempotent replay of a stored reply, tokens are **single
characters** of the stored text.

#### `crisis_alert`

Emitted instead of a normal generation when the user message contains a
crisis signal. Then `done`.

```json
{
  "title": "Immediate Support Available",
  "message": "<language-specific lead-in plus helplines>",
  "helplines": [
    {
      "name": "Tele-MANAS",
      "number": "14416",
      "description": "24/7 Toll-free mental health helpline"
    }
  ],
  "action_card": {
    "card_type": "BOOKING_CARD",
    "title": "Connect to Professional Crisis Support",
    "subtitle": "Speak to a trained counselor immediately",
    "action_payload": { "flow": "crisis_helpline_call" }
  }
}
```

Helpline `number` values come from server config. Do not translate them.

#### `action_card`

One event per card after generation. Payload is `ActionCard.model_dump()`
(same fields as the JSON chat `action_cards` item).

#### `error`

```json
{ "error": "Unable to start response stream", "retryable": true }
```

or

```json
{ "error": "Response stream interrupted", "retryable": true }
```

First form: both providers failed before any token. Second: stream
broke after tokens were sent (the client may retry; the server does
not stitch a second model onto a partial reply).

#### `done`

```json
{ "status": "completed" }
```

Always the last event on a finished stream (including crisis).

There is no `heartbeat` event.

### Frontend Notes

Read `event` + `data`. Do not assume a `success` field. After `error`
with `retryable: true`, you may POST again.

**Legacy:** `POST /chat/stream`

---

# Report APIs

## POST /api/v1/session/report

### Purpose
Post-conversation reading for `session_id`. May include at most one
meditation recommendation. Identity is the token (no body `user_id`).

### Authentication
Bearer token required. Uses the authenticated user's identity.

### Request

Body:

```json
{
  "session_id": "session_123"
}
```

### Parameters

| Field | Type | Required | Description |
|---|---|---|---|
| session_id | string | Yes | Session to read |

### Success Response — 200

```json
{
  "session_id": "session_123",
  "summary": "Plain-language session summary",
  "recommendation": {
    "decision": "RECOMMEND_MEDITATION",
    "meditation_id": "med_abc",
    "title": "Evening wind-down",
    "category": "sleep",
    "duration_seconds": 300,
    "reason": "A short reset after a heavy day",
    "action_card": {}
  },
  "withheld_reason": "",
  "psychiatric_summary": "Short clinical-style reading",
  "psychiatric_metric": 6,
  "events": [
    { "event_id": "evt_a1b2c3d4", "label": "Board exam next week", "resolved": false }
  ],
  "tasks": [
    {
      "id": "proposal_ab12cd34",
      "title": "Pack the exam bag tonight",
      "description": "",
      "added": false
    }
  ],
  "task_persistence": "proposed",
  "memory_facts": { "inserted": 1, "confirmed": 0 }
}
```

`recommendation` is `null` when nothing is offered. Then
`withheld_reason` may be a non-empty string.

`psychiatric_metric` is an integer 1–10 or omitted/null if unset.

`memory_facts` is `{ "inserted", "confirmed" }` after optional fact
write (consent-gated).

### Errors

#### 400

`This session has no conversation to report on` (and similar).

401, 429.

### Frontend Notes

Tasks are **proposals**. Call accept (below) to put one on today's list.

**Legacy:** `POST /api/session/report`

---

## POST /api/v1/reports/tasks/accept

### Purpose
Copy one proposed report task onto today's task list.

### Authentication
Bearer token required. Optional body `user_id` must match the token if sent.

### Request

```json
{
  "session_id": "session_123",
  "task_id": "proposal_ab12cd34"
}
```

`user_id` is optional and must match the token.

### Success Response — 200

```json
{
  "task": {
    "id": "proposal_ab12cd34",
    "title": "Pack the exam bag tonight",
    "description": "",
    "added": true,
    "manager_task_id": "task_1a2b3c4d"
  },
  "task_persistence": "saved"
}
```

If already added: `task_persistence` is `existing`.

### Errors

403, 404 (`Report not found` / `Task not found`), 400.

**Legacy:** `POST /api/reports/tasks/accept`

---

# Sleep APIs

## POST /api/v1/sleep

### Purpose
Store one night. Optional body `user_id` cannot retarget.

### Authentication
Bearer token required.

### Request

```json
{
  "bedtime": "22:30",
  "wake_up_time": "06:30",
  "date": "2026-09-26",
  "total_duration_minutes": 480
}
```

`total_duration_minutes` and `user_id` are optional.

### Success Response — 200

```json
{
  "user_id": "usr_ab12cd34",
  "bedtime": "22:30",
  "wake_up_time": "06:30",
  "total_duration_minutes": 480,
  "date": "2026-09-26",
  "created_at": "2026-09-26T01:00:00+00:00"
}
```

### Errors

400, 403.

**Legacy:** `POST /api/sleep`

---

## GET /api/v1/sleep/recent

### Purpose
Latest night, or `null`.

### Success Response — 200

```json
{
  "sleep": {
    "user_id": "usr_ab12cd34",
    "bedtime": "22:30",
    "wake_up_time": "06:30",
    "total_duration_minutes": 480,
    "date": "2026-09-26",
    "created_at": "2026-09-26T01:00:00+00:00"
  }
}
```

**Legacy:** `GET /api/sleep/recent`

---

## GET /api/v1/sleep/history

### Query

| Parameter | Type | Required | Description |
|---|---|---|---|
| days | integer | No | Default 7, clamped to 1–90 |

### Success Response — 200

```json
{
  "sleep": []
}
```

Each item is the same object as `sleep` above.

**Legacy:** `GET /api/sleep/history`

---

# Journal APIs

List endpoints return **previews** (`content` truncated to
`JOURNAL_PREVIEW_CHARS`, default 180). Full text is only on
`GET /journal/entry/{entry_id}` and on create.

Journal `mood` is a free string, not an enum.

## POST /api/v1/journal/entry

### Request

```json
{
  "title": "Evening note",
  "content": "The mock felt heavier than I expected.",
  "mood": "anxious",
  "tags": ["exam"],
  "time_spent": 4
}
```

`tags`, `time_spent` (default 0), and `user_id` are optional.

### Success Response — 200

```json
{
  "entry_id": "66f1a2b3c4d5e6f7a8b9c0d1",
  "user_id": "usr_ab12cd34",
  "mood": "anxious",
  "title": "Evening note",
  "content": "The mock felt heavier than I expected.",
  "tags": ["exam"],
  "time_spent": 4,
  "is_favorite": false,
  "favorited_at": null,
  "timestamp": "2026-09-26T15:00:00+00:00",
  "created_at": "2026-09-26T15:00:00+00:00",
  "updated_at": "2026-09-26T15:00:00+00:00"
}
```

A duplicate post within two minutes may include `"duplicate": true`.

### Errors

400, 403.

**Legacy:** `POST /journal/entry`

---

## GET /api/v1/journal/recent-entries

### Success Response — 200

```json
{
  "entries": []
}
```

Each entry is the public journal object with truncated `content`.

**Legacy:** `GET /journal/recent-entries`

---

## GET /api/v1/journal/entry/{entry_id}

### Path

| Parameter | Type | Required | Description |
|---|---|---|---|
| entry_id | string | Yes | Mongo ObjectId string |

### Success Response — 200

Full public journal object (no preview truncation).

### Errors

#### 404

`Journal entry not found`

**Legacy:** `GET /journal/entry/{entry_id}`

---

## GET /api/v1/journal/past-reflections

Same envelope as recent-entries: `{ "entries": [ ... ] }`.

**Legacy:** `GET /journal/past-reflections`

---

## GET /api/v1/journal/calendar-data

### Success Response — 200

```json
{
  "days": {
    "2026-09-26": 2
  }
}
```

`days` is a map of `YYYY-MM-DD` → entry count.

**Legacy:** `GET /journal/calendar-data`

---

## GET /api/v1/journal/favorites

`{ "entries": [ ... ] }` (previews).

**Legacy:** `GET /journal/favorites`

---

## GET /api/v1/journal/stats

```json
{
  "entry_count": 12,
  "favorite_count": 3
}
```

**Legacy:** `GET /journal/stats`

---

## GET /api/v1/journal/monthly-mindfulness

```json
{
  "month": "2026-09",
  "entry_count": 4,
  "mood_counts": { "anxious": 2, "calm": 2 }
}
```

Selected moods for the current UTC month — not an inferred score.

**Legacy:** `GET /journal/monthly-mindfulness`

---

# Task APIs

Task UI objects:

```json
{
  "id": "task_1a2b3c4d",
  "title": "Pack the exam bag tonight",
  "description": "",
  "completed": false,
  "is_custom": false
}
```

Path `claimed_user_id` must match the token.

## GET /api/v1/report_card/tasks/{claimed_user_id}

### Success Response — 200

```json
{
  "user_id": "usr_ab12cd34",
  "date": "2026-09-26",
  "tasks": []
}
```

### Errors

403.

**Legacy:** `GET /api/report_card/tasks/{claimed_user_id}`

---

## POST /api/v1/report_card/tasks/complete

```json
{
  "task_id": "task_1a2b3c4d"
}
```

Optional `user_id` must match the token.

### Success Response — 200

The completed public task (`completed: true`).

### Errors

403, 404, 400.

**Legacy:** `POST /api/report_card/tasks/complete`

---

## POST /api/v1/report_card/tasks/custom

```json
{
  "title": "Walk after dinner",
  "description": "Ten minutes"
}
```

`description` defaults to `""`.

### Success Response — 200

New public task with `is_custom: true`.

**Legacy:** `POST /api/report_card/tasks/custom`

---

## PATCH /api/v1/report_card/tasks/custom/{claimed_user_id}/{task_id}

```json
{
  "title": "Walk after dinner",
  "is_deleted": false
}
```

All body fields optional: `title`, `description`, `is_deleted`.

### Success Response — 200

Updated public task.

**Legacy:** `PATCH /api/report_card/tasks/custom/{claimed_user_id}/{task_id}`

---

# Meditation APIs

There is no HTTP route that streams audio files.

## POST /api/v1/meditation/preview

### Purpose
Developer preview of the ranker. Includes a `debug` object that chat
cards do **not** send.

### Request

```json
{
  "message": "I cannot sleep"
}
```

`message` is optional.

### Success Response — 200

```json
{
  "probe_message": "I cannot sleep",
  "decision": "RECOMMEND_MEDITATION",
  "meditation_id": "med_abc",
  "title": "Evening wind-down",
  "category": "sleep",
  "duration_seconds": 300,
  "reason": "A short reset",
  "audio_available": true,
  "friction_level": null,
  "debug": {}
}
```

**Frontend usability improvement recommended:** `debug` is internal
ranking detail. Do not show it in the product UI.

**Legacy:** `POST /api/meditation/preview`

---

## POST /api/v1/meditation/start

```json
{
  "meditation_id": "med_abc",
  "execution_nonce": "nonce_from_card",
  "session_id": "session_123",
  "reason": "A short reset"
}
```

`session_id` and `reason` are optional.

### Success Response — 200

```json
{
  "execution_id": "mexe_ab12cd34ef56",
  "meditation_id": "med_abc",
  "status": "STARTED",
  "execution_nonce": "nonce_from_card",
  "user_helpfulness_feedback": null,
  "listen_duration_seconds": 0
}
```

### Errors

400 (`Unknown meditation`, `execution_nonce is required`, nonce clash).

**Legacy:** `POST /api/meditation/start`

---

## POST /api/v1/meditation/complete

```json
{
  "execution_id": "mexe_ab12cd34ef56",
  "execution_nonce": "nonce_from_card",
  "listen_duration_seconds": 180
}
```

`listen_duration_seconds` defaults to 0.

### Success Response — 200

Same public execution object; `status` is `COMPLETED`.

### Errors

404.

**Legacy:** `POST /api/meditation/complete`

---

## POST /api/v1/meditation/feedback

```json
{
  "execution_id": "mexe_ab12cd34ef56",
  "execution_nonce": "nonce_from_card",
  "feedback": "HELPFUL"
}
```

### Success Response — 200

Public execution with `user_helpfulness_feedback` set.

### Errors

400 if `feedback` is not `HELPFUL` / `NOT_HELPFUL` / `DISMISSED`.
404 otherwise.

**Legacy:** `POST /api/meditation/feedback`

---

# Language APIs

## POST /api/v1/language

### Purpose
Store `users.preferred_language`. Uses the token owner.

### Request

```json
{
  "language": "HINDI"
}
```

Optional `user_id` must match the token.

### Success Response — 200

```json
{
  "preferred_language": "HINDI",
  "preferred_language_updated_at": "2026-09-26T15:00:00+00:00"
}
```

### Errors

400 (unknown language), 403, 404 (`Active user not found`).

**Legacy:** `POST /api/language`

---

# Memory APIs

## POST /api/v1/memory/consent

```json
{
  "enabled": true
}
```

### Success Response — 200

```json
{
  "enabled": true
}
```

404 if the user row is missing.

**Legacy:** `POST /api/memory/consent`

---

## POST /api/v1/memory/feedback

APM intervention feedback. Only explicit HELPFUL / NOT_HELPFUL (and
STARTED / COMPLETED) are accepted.

```json
{
  "edge_id": "edge_123",
  "intervention_id": "int_123",
  "execution_nonce": "nonce_value",
  "event_type": "HELPFUL",
  "before_state": 0.2,
  "after_state": 0.4
}
```

`before_state` / `after_state` are optional numbers in `[-1.0, 1.0]`.

### Success Response — 200

```json
{
  "recorded": true
}
```

### Errors

400 unsupported `event_type`, 403.

**Legacy:** `POST /api/memory/feedback`

---

## DELETE /api/v1/memory

Deletes derived memory (APM, patterns, student facts, meditation
executions/offers). Chat history and Graph RAG stay.

### Success Response — 200

```json
{
  "deleted": {},
  "patterns_deleted": {},
  "student_facts_deleted": 0,
  "meditation_executions_deleted": 0
}
```

`deleted` / `patterns_deleted` are count maps from those stores.

**Frontend usability improvement recommended:** shape is a mix of
objects and integers.

**Legacy:** `DELETE /api/memory`

---

## POST /api/v1/memory/consolidate

Rebuilds the profile summary from stored facts. No body.

### Success Response — 200

```json
{
  "kept": 4,
  "archived": 1,
  "summary_chars": 180
}
```

**Legacy:** `POST /api/memory/consolidate`

---

# Pattern APIs

## POST /api/v1/patterns/feedback

```json
{
  "pattern_id": "pat_ab12cd34ef56",
  "event_type": "CONFIRM",
  "note": "Yes, that fits"
}
```

`note` is optional.

### Success Response — 200

```json
{
  "pattern_id": "pat_ab12cd34ef56",
  "confidence": 0.62,
  "status": "EMERGING",
  "confirm_count": 1,
  "disagree_count": 0
}
```

`status` is a pattern status enum value.

### Errors

404 if the pattern is missing.

**Legacy:** `POST /api/patterns/feedback`

---

# Academic APIs

None. Marks are not writable or readable over HTTP.

# Attendance APIs

None.

---

# Consultation APIs

## GET /api/v1/consultation-evaluation/status

### Purpose
The signed-in user's latest decision. No query params.

### Success Response — 200

```json
{
  "status": "NOT_EVALUATED",
  "care_recommendation": null,
  "evaluation_timestamp": null,
  "unread_notifications": 0
}
```

When an evaluation exists, `status` is `REFERRED`, `MONITORING`, or
`NOT_NEEDED`. `unread_notifications` is a count.

**Legacy:** `GET /consultation-evaluation/status`

---

## POST /api/v1/consultation-evaluation/manual

Runs an evaluation for the token owner. Staff may pass another
`user_id`.

```json
{}
```

or `{ "user_id": "usr_other" }` (staff only).

### Success Response — 200

```json
{
  "evaluation_id": "eval_ab12",
  "user_id": "usr_ab12cd34",
  "status": "NOT_NEEDED",
  "care_recommendation": "No indication of need for professional consultation.",
  "reasons": [],
  "trigger": "MANUAL",
  "evaluation_timestamp": "2026-09-26T15:00:00+00:00",
  "cooldown_active": false,
  "cooldown_until": null,
  "notification_written": false
}
```

### Errors

403 if naming another user without a staff role.

**Legacy:** `POST /consultation-evaluation/manual`

---

## POST /api/v1/consultation-evaluation/manual-override

Staff only.

```json
{
  "user_id": "usr_ab12cd34",
  "status": "MONITORING",
  "reason": "Reviewed in clinic today"
}
```

`reason` must be at least 5 characters (domain rule). `status` must be
one of `REFERRED`, `MONITORING`, `NOT_NEEDED`.

### Errors

403 `Staff role required`, 400.

**Legacy:** `POST /consultation-evaluation/manual-override`

---

## POST /api/v1/consultation-evaluation/batch

Staff only.

```json
{
  "user_ids": ["usr_ab12cd34"]
}
```

### Success Response — 200

```json
{
  "count": 1,
  "results": []
}
```

`results` items are public evaluations, or
`{ "user_id": "...", "error": "evaluation_failed" }`.

**Legacy:** `POST /consultation-evaluation/batch`

---

# Tracking APIs

## POST /api/v1/mood

Only `mood` is required.

```json
{
  "mood": "anxious",
  "score": 3,
  "note": "before the mock",
  "input_format": "EMOJI",
  "client_event_id": "offline-1"
}
```

`logged_at` is optional ISO datetime (offline backfill). Future times
are pulled to now. Optional `user_id` must match the token.

### Success Response — 200

```json
{
  "entry": {
    "mood": "anxious",
    "score": 3,
    "note": "before the mock",
    "input_format": "EMOJI",
    "logged_at": "2026-09-26T09:12:46.651874+00:00"
  },
  "duplicate": false,
  "crisis": false
}
```

A repeated `client_event_id` returns the first row with
`duplicate: true`. `crisis: true` means the note had crisis wording;
still stored.

### Errors

400 (empty mood), 403.

**Legacy:** `POST /api/mood`

---

## GET /api/v1/mood/recent

| Parameter | Type | Required | Description |
|---|---|---|---|
| days | integer | No | Default 7, clamped 1–90 |

```json
{
  "moods": [
    {
      "mood": "anxious",
      "score": 3,
      "note": "before the mock",
      "input_format": "EMOJI",
      "logged_at": "2026-09-26T09:12:46.651874+00:00"
    }
  ]
}
```

**Legacy:** `GET /api/mood/recent`

---

## GET /api/v1/habits

| Parameter | Type | Required | Description |
|---|---|---|---|
| include_archived | boolean | No | Default false |

```json
{
  "show_streaks": false,
  "habits": [
    {
      "habit_id": "habit_8f7f4f07",
      "title": "Morning walk",
      "frequency": "daily",
      "status": "active",
      "reminder_time": "",
      "completed_today": false,
      "streak_hidden": true
    }
  ]
}
```

If streaks are on, `streak` (integer) is present and `streak_hidden`
is absent.

**Legacy:** `GET /api/habits`

---

## POST /api/v1/habits

```json
{
  "title": "Morning walk",
  "frequency": "daily",
  "reminder_time": "07:00"
}
```

`frequency` defaults to `daily`. `reminder_time` defaults to `""`.

### Success Response — 200

One public habit object (same fields as list items).

### Errors

400 (name too short, bad frequency/time, too many active habits), 403.

**Legacy:** `POST /api/habits`

---

## PATCH /api/v1/habits/{habit_id}

```json
{
  "status": "paused"
}
```

Optional: `title`, `frequency`, `reminder_time`, `status`.

**Legacy:** `PATCH /api/habits/{habit_id}`

---

## POST /api/v1/habits/{habit_id}/check-in

```json
{}
```

or `{ "on_date": "2026-09-25" }`.

### Success Response — 200

```json
{
  "habit": {},
  "already_logged": false
}
```

`habit` is the public habit. Second check-in the same day:
`already_logged: true`.

### Errors

404 unknown habit, 400 archived / bad date.

**Legacy:** `POST /api/habits/{habit_id}/check-in`

---

## POST /api/v1/habits/streaks

```json
{
  "show_streaks": true
}
```

```json
{
  "show_streaks": true,
  "habits_updated": 1
}
```

Opting out hides streaks from the chatbot as well as the UI.

**Legacy:** `POST /api/habits/streaks`

---

# Voice APIs

Two different features. Do not auto-send a transcription into chat.

See also [VOICE_ARCHITECTURE.md](../voice/VOICE_ARCHITECTURE.md).

## POST /api/v1/voice/stt

### Purpose
Transcribe one microphone clip so the user can review it in the chat input.

### Authentication
Bearer token required.

Uses the authenticated user's identity. Do not send `user_id`.

### Request

Headers:

```http
Authorization: Bearer <ACCESS_TOKEN>
```

`Content-Type` is multipart (the client sets the boundary). Field name
must be `audio`.

Path parameters: none

Query parameters: none

Body: multipart file `audio` (WAV preferred). Optional filename / MIME
are ignored for trust — the server inspects the bytes.

### Parameters

| Field | Type | Required | Description |
|---|---|---|---|
| audio | file | Yes | Speech clip. Converted once to 16-bit PCM 16 kHz mono WAV |

Limits: `MAX_AUDIO_FILE_SIZE` (default 10 MiB),
`MAX_AUDIO_DURATION_SECONDS` (default 60).

### Success Response — 200

```json
{
  "success": true,
  "text": "I've been feeling really overwhelmed today."
}
```

This is one of the few success bodies that includes `success: true`.

### Response Fields

| Field | Type | Description |
|---|---|---|
| success | boolean | Always `true` on 200 |
| text | string | Transcript. Put this in the input box. Do not POST `/chat/send` until the user confirms |

### Errors

#### 401

```json
{
  "success": false,
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Bearer token required",
    "request_id": "a28f57fc36194b50a4249822939991bb"
  },
  "detail": "Bearer token required"
}
```

#### 400

Empty, non-WAV, or oversized audio. `error.code` is `INVALID_REQUEST`.

#### 502

Sarvam STT failed or is unconfigured. `error.code` is `PROVIDER_ERROR`.

#### 429

`Too many requests.`

### Frontend Notes

Show `text` in the composer. The user must edit or send explicitly.
STT does not change `users.preferred_language`. Language detection is
automatic (no hint is sent to Sarvam).

**Legacy:** `POST /voice/stt`

---

## WS /api/v1/ws/psychiatrist-voice

### Purpose
Real-time spoken conversation. Same Zenark brain as `/chat/send`.

### Authentication
Bearer token required.

```http
Authorization: Bearer <ACCESS_TOKEN>
```

Browsers may use:

```text
ws://localhost:8000/api/v1/ws/psychiatrist-voice?token=<ACCESS_TOKEN>
```

Do not authorize with `user_id`.

### Request

After the handshake, send JSON control events. After `audio.start`,
send binary PCM16 (16 kHz mono) or one WAV blob.

```json
{"type": "session.start", "chat_session_id": "session_123"}
```

```json
{"type": "audio.start"}
```

(binary audio frames)

```json
{"type": "audio.end"}
```

```json
{"type": "turn.end"}
```

```json
{"type": "cancel"}
```

```json
{"type": "session.end"}
```

Path parameters: none

Query parameters:

| Parameter | Type | Required | Description |
|---|---|---|---|
| token | string | If no Authorization header | Access token |

### Success events

`session.started`

```json
{
  "type": "session.started",
  "voice_session_id": "voice_ab12cd34ef56abcd",
  "chat_session_id": "session_123",
  "request_id": "a28f57fc36194b50a4249822939991bb"
}
```

`transcript.final`

```json
{
  "type": "transcript.final",
  "text": "I've been feeling really overwhelmed today."
}
```

`assistant.text`

```json
{
  "type": "assistant.text",
  "text": "That sounds like a lot to carry today.",
  "action_cards": []
}
```

`audio.chunk`

```json
{
  "type": "audio.chunk",
  "encoding": "pcm_s16le",
  "sample_rate": 24000,
  "channels": 1,
  "data": "<base64 PCM>"
}
```

`assistant.done`

```json
{
  "type": "assistant.done",
  "chat_session_id": "session_123",
  "stt_latency_ms": 210,
  "llm_latency_ms": 1400,
  "tts_latency_ms": 380,
  "first_audio_latency_ms": 360,
  "total_turn_latency_ms": 2100
}
```

`transcript.partial` is not sent. STT runs on `turn.end` for the full
utterance.

### Response Fields

| Field | Type | Description |
|---|---|---|
| type | string | Event name |
| voice_session_id | string | Live adapter id |
| chat_session_id | string | Existing chat session; messages persist here |
| text | string | Transcript or assistant reply |
| action_cards | array | Same cards as `/chat/send` |
| data | string | Base64 PCM16 LE, 24 kHz, mono |
| encoding | string | `pcm_s16le` |

### Errors

Handshake without a token closes with code **4401**.

Foreign `voice_session_id` or mismatched `user_id`:

```json
{
  "type": "error",
  "code": "FORBIDDEN",
  "message": "You cannot access that resource.",
  "retryable": false,
  "request_id": "a28f57fc36194b50a4249822939991bb"
}
```

STT failure: `code=STT_FAILED`, `retryable=true`.  
TTS failure: assistant text is still sent, then `code=TTS_FAILED`.  
LLM timeout: `code=LLM_TIMEOUT`, `retryable=true`.

### Frontend Notes

Play `audio.chunk` as 16-bit PCM at 24 kHz. Show `action_cards` beside
the voice UI. Reconnect with the same `chat_session_id` to keep history.
One live voice socket per user.

**Legacy:** `WS /ws/psychiatrist-voice`

---

# Health APIs

## GET /api/v1/health

Not a dependency check.

```json
{
  "status": "healthy",
  "service": "therapeutic-ai-chatbot",
  "version": "0.4.0"
}
```

**Legacy:** `GET /health`

---

## GET /api/v1/health/live

```json
{
  "status": "live"
}
```

**Legacy:** `GET /health/live`

---

## GET /api/v1/health/ready

```json
{
  "status": "ready"
}
```

### 503

```json
{
  "status": "not_ready",
  "reason": "mongo_unavailable"
}
```

`reason` may also be `mongo_unconfigured`. This 503 is **not** the
standard error envelope.

**Legacy:** `GET /health/ready`

---

# Deprecated / Compatibility APIs

The backend does not set a `deprecated` flag on these routes. They are
the same handlers remounted for existing clients (including Streamlit).
New apps should use `/api/v1`.

| Old Endpoint | Canonical Endpoint | Status |
|---|---|---|
| POST /auth/login | POST /api/v1/auth/login | Compatibility |
| POST /auth/signup | POST /api/v1/auth/signup | Compatibility |
| POST /chat/welcome | POST /api/v1/chat/welcome | Compatibility |
| POST /chat/send | POST /api/v1/chat/send | Compatibility |
| POST /chat/stream | POST /api/v1/chat/stream | Compatibility |
| GET /chat/session/{user_id}/resume | GET /api/v1/chat/session/{user_id}/resume | Compatibility |
| POST /journal/entry | POST /api/v1/journal/entry | Compatibility |
| GET /journal/recent-entries | GET /api/v1/journal/recent-entries | Compatibility |
| GET /journal/entry/{entry_id} | GET /api/v1/journal/entry/{entry_id} | Compatibility |
| GET /journal/past-reflections | GET /api/v1/journal/past-reflections | Compatibility |
| GET /journal/calendar-data | GET /api/v1/journal/calendar-data | Compatibility |
| GET /journal/favorites | GET /api/v1/journal/favorites | Compatibility |
| GET /journal/stats | GET /api/v1/journal/stats | Compatibility |
| GET /journal/monthly-mindfulness | GET /api/v1/journal/monthly-mindfulness | Compatibility |
| POST /api/sleep | POST /api/v1/sleep | Compatibility |
| GET /api/sleep/recent | GET /api/v1/sleep/recent | Compatibility |
| GET /api/sleep/history | GET /api/v1/sleep/history | Compatibility |
| POST /api/mood | POST /api/v1/mood | Compatibility |
| GET /api/mood/recent | GET /api/v1/mood/recent | Compatibility |
| GET /api/habits | GET /api/v1/habits | Compatibility |
| POST /api/habits | POST /api/v1/habits | Compatibility |
| PATCH /api/habits/{habit_id} | PATCH /api/v1/habits/{habit_id} | Compatibility |
| POST /api/habits/{habit_id}/check-in | POST /api/v1/habits/{habit_id}/check-in | Compatibility |
| POST /api/habits/streaks | POST /api/v1/habits/streaks | Compatibility |
| GET /api/report_card/tasks/{claimed_user_id} | GET /api/v1/report_card/tasks/{claimed_user_id} | Compatibility |
| POST /api/report_card/tasks/complete | POST /api/v1/report_card/tasks/complete | Compatibility |
| POST /api/report_card/tasks/custom | POST /api/v1/report_card/tasks/custom | Compatibility |
| PATCH /api/report_card/tasks/custom/{claimed_user_id}/{task_id} | PATCH /api/v1/report_card/tasks/custom/{claimed_user_id}/{task_id} | Compatibility |
| POST /api/reports/tasks/accept | POST /api/v1/reports/tasks/accept | Compatibility |
| POST /api/session/report | POST /api/v1/session/report | Compatibility |
| POST /api/meditation/preview | POST /api/v1/meditation/preview | Compatibility |
| POST /api/meditation/start | POST /api/v1/meditation/start | Compatibility |
| POST /api/meditation/complete | POST /api/v1/meditation/complete | Compatibility |
| POST /api/meditation/feedback | POST /api/v1/meditation/feedback | Compatibility |
| POST /api/language | POST /api/v1/language | Compatibility |
| POST /api/memory/consent | POST /api/v1/memory/consent | Compatibility |
| POST /api/memory/feedback | POST /api/v1/memory/feedback | Compatibility |
| DELETE /api/memory | DELETE /api/v1/memory | Compatibility |
| POST /api/memory/consolidate | POST /api/v1/memory/consolidate | Compatibility |
| POST /api/patterns/feedback | POST /api/v1/patterns/feedback | Compatibility |
| POST /consultation-evaluation/manual | POST /api/v1/consultation-evaluation/manual | Compatibility |
| POST /consultation-evaluation/manual-override | POST /api/v1/consultation-evaluation/manual-override | Compatibility |
| POST /consultation-evaluation/batch | POST /api/v1/consultation-evaluation/batch | Compatibility |
| GET /consultation-evaluation/status | GET /api/v1/consultation-evaluation/status | Compatibility |
| GET /health | GET /api/v1/health | Compatibility |
| GET /health/live | GET /api/v1/health/live | Compatibility |
| GET /health/ready | GET /api/v1/health/ready | Compatibility |
| POST /voice/stt | POST /api/v1/voice/stt | Compatibility |
| WS /ws/psychiatrist-voice | WS /api/v1/ws/psychiatrist-voice | Compatibility |

---

# Frontend Integration Flows

### Login → Start Chat

```text
POST /api/v1/auth/login
        ↓
Store access_token + user_id
        ↓
POST /api/v1/chat/welcome   { user_id, session_id }
        ↓
POST /api/v1/chat/send      { user_id, session_id, message }
   or POST /api/v1/chat/stream
```

### Resume

```text
GET /api/v1/chat/session/{user_id}/resume
        ↓
Render recent_messages
        ↓
POST /api/v1/chat/send | stream
```

### Generate Report

```text
User finishes a session
        ↓
POST /api/v1/session/report   { session_id }
        ↓
Show psychiatric_summary, events, proposed tasks
        ↓
POST /api/v1/reports/tasks/accept   { session_id, task_id }
        ↓
GET /api/v1/report_card/tasks/{user_id}
```

### Meditation

```text
Receive recommendation.action_card (or preview)
        ↓
POST /api/v1/meditation/start   { meditation_id, execution_nonce }
        ↓
Play audio in the client (no media URL from this API)
        ↓
POST /api/v1/meditation/complete
        ↓
POST /api/v1/meditation/feedback   { feedback: HELPFUL | NOT_HELPFUL | DISMISSED }
```

### Language

```text
POST /api/v1/language   { language: "HINDI" }
        ↓
Later chat/report replies use that stored preference
```

### Microphone STT → text send

```text
POST /api/v1/voice/stt   (multipart audio)
        ↓
Show `text` in the input
        ↓
User edits
        ↓
POST /api/v1/chat/send
```

### Voice Mode

```text
WS /api/v1/ws/psychiatrist-voice
        ↓
session.start  { chat_session_id }
        ↓
audio.start → PCM/WAV → audio.end → turn.end
        ↓
transcript.final + assistant.text + audio.chunk + assistant.done
        ↓
session.end
```

### Mood + habit

```text
POST /api/v1/mood   { mood }
        ↓
POST /api/v1/habits   { title }
        ↓
POST /api/v1/habits/{habit_id}/check-in   {}
```

---

# Frontend Implementation Notes

1. Base URL + `/api/v1`.
2. JSON `Content-Type` on bodies. Empty `{}` is valid for habit check-in.
3. Bearer token on every non-public route.
4. Do not authorize with a client-supplied `user_id`.
5. Parse the error envelope; also read `detail` if you already do.
6. Expect 400 for bad JSON/schema, not 422.
7. Stream: handle `token`, `crisis_alert`, `action_card`, `error`, `done`.
8. No public audio/file URL. `audio_available` is a flag only.
    Voice Mode PCM arrives on the socket (`audio.chunk`), not as a file URL.
9. Dates: `YYYY-MM-DD` and ISO-8601 UTC timestamps.
10. Retries: mood `client_event_id` and habit same-day check-in are
    idempotent. Chat stream `error.retryable` means you may POST again.
    Do not retry login blindly (rate limit 20/min).
11. Interactive API explorer: `http://localhost:8000/docs`.

# School dashboard

Staff-only. Bearer token required. The school comes from the account. Students, teachers, and generic staff receive 403. Success envelope is `{success, data, meta}`. Errors use the normal error envelope. Details: `docs/dashboard-api.md`.

## GET /api/v1/dashboard/context

Signed-in user, school, role, and permission names.

## GET /api/v1/dashboard/academic-years

Years present on stored marks, plus the calendar year labeled as calendar.

## GET /api/v1/dashboard/grades

Grade ids derived from student class labels, with counts.

## GET /api/v1/dashboard/classes

Query: `grade_id`. Classes in the signed-in school.

## GET /api/v1/dashboard/subjects

Query: `grade_id`, `class_id`. Subject names taken from `marks`.

## GET /api/v1/dashboard/overview

Query: `academic_year`, `grade_id`, `class_id`, `subject_id`, `from`, `to`.

## GET /api/v1/dashboard/classes/{class_id}/overview

Class indexes, quadrant counts, strengths, and students needing attention.

## GET /api/v1/dashboard/classes/{class_id}/performance-quadrant

Stars, plateaued, climbers, critical. See the quadrant method in `docs/dashboard-api.md`.

## GET /api/v1/dashboard/classes/{class_id}/students

Query: `page`, `limit`, `cursor`.

## GET /api/v1/dashboard/classes/{class_id}/subjects/{subject_id}/overview

Class and school averages from marks. Teacher rating and concept mastery are null when those collections do not exist.

## GET /api/v1/dashboard/students/{student_id}/profile

Academic, attendance, aggregated wellbeing, risk band. No journal text.

## GET /api/v1/dashboard/students/{student_id}/interventions

Intervention plans for one student in this school.

## POST /api/v1/dashboard/students/{student_id}/parent-contact

Body: `{ "message", "reason" }`. Recorded. Not delivered. No parent address is returned.

## POST /api/v1/dashboard/students/{student_id}/notify-counselor

Body: `{ "message" }`. Notifies counselor accounts in the same school.

## GET /api/v1/dashboard/risk/summary

Counts for critical, at_risk, and watch.

## GET /api/v1/dashboard/risk/students

Query: `severity`, `grade_id`, `class_id`, `subject_id`, `academic_year`, `from`, `to`, `page`, `limit`, `cursor`.

## POST /api/v1/dashboard/interventions

Body: `{ "student_id", "title", "plan", "status", "assignee_user_id" }`.

## GET /api/v1/dashboard/interventions/{intervention_id}

One plan in the signed-in school.

## PATCH /api/v1/dashboard/interventions/{intervention_id}

Partial update of title, plan, status, or assignee.

## GET /api/v1/dashboard/teachers

Query: `subject_id`, `class_id`, `academic_year`, `page`, `limit`, `cursor`.

## GET /api/v1/dashboard/teachers/{teacher_id}

Profile, listed subjects and classes, stored actions. `rating` is null.

## POST /api/v1/dashboard/teachers/{teacher_id}/messages

Body: `{ "message" }`. A `rating` field is rejected.

## POST /api/v1/dashboard/teachers/{teacher_id}/reviews

Body: `{ "notes", "focus_areas" }`. No numeric rating is stored.

## POST /api/v1/dashboard/teachers/{teacher_id}/support-plan

Body: `{ "summary", "focus_areas" }`.

## GET /api/v1/dashboard/analytics/grade-trends

Weekly mark averages by grade.

## GET /api/v1/dashboard/analytics/subject-performance

Subject, average, growth, trend.

## GET /api/v1/dashboard/analytics/wellbeing

School aggregates only. No individual notes.

## GET /api/v1/dashboard/analytics/comparative

`benchmark` is null until a benchmark collection exists.

## GET /api/v1/dashboard/notifications

Query: `unread_only`, `page`, `limit`, `cursor`.

## POST /api/v1/dashboard/notifications/{notification_id}/read

Marks one visible notification read.

## POST /api/v1/dashboard/notifications/read-all

Marks the caller's visible unread notifications read.

## GET /api/v1/dashboard/events

Server-sent events for this school only. First bytes are a `: connected` comment.

## POST /api/v1/dashboard/reports

Body: `{ "type": "parent"|"board"|"wellbeing", "scope", "grade_id", "class_id", "student_id", "academic_year", "include_ai_insights", "include_charts" }`. Returns 202. Charts are not rendered.

## GET /api/v1/dashboard/reports

Report jobs for this school.

## GET /api/v1/dashboard/reports/{report_id}

Job status.

## GET /api/v1/dashboard/reports/{report_id}/preview

Summary once `status` is `ready`. Otherwise 409.

## GET /api/v1/dashboard/reports/{report_id}/download

JSON download once ready. Otherwise 409. Another school receives 404.

## POST /api/v1/dashboard/assistant

Body: `{ "message", "context": { "grade_id", "class_id", "subject_id", "academic_year" } }`. 403 when `ai_insights` is false.

## GET /api/v1/dashboard/settings

Notification and assistant preferences, including which ones are only stored.

## PATCH /api/v1/dashboard/settings

Body: any of `at_risk_alerts`, `weekly_digest`, `report_generation_alerts`, `ai_insights`.

