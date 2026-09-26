# Zenark API Inventory — Phase 1 Audit

> **Status:** Audit only. No routes changed. Branch `production-readiness/refactor`.
> **Source of truth:** `app.py` at the time of audit (46 decorated endpoints, all on the
> bare `app` object — there are **no `APIRouter`s yet**). Schemas from `schemas.py`.
> **Scope note:** "1,000,000" is a scalability *target*, not a measured capacity. Nothing
> in this document claims proven throughput.

## How to read this

- **Auth**: `PUBLIC` (no token), `AUTHENTICATED` (valid Bearer), `ADMIN` (staff role
  required), `INTERNAL` (not meant for public clients).
- **Ownership**: how the row's owner is decided. Three inconsistent patterns exist today
  (see [Cross-cutting findings](#cross-cutting-findings)):
  - `assert_owner(body)` — route compares `payload.user_id` to the token, 403 on mismatch.
  - `service(claimed)` — route passes `payload.user_id`/path as `claimed_user_id`; the
    domain layer validates it against the token's identity aliases.
  - `token-only` — body/path `user_id` is ignored; the token alone selects the owner.
  - `path` — a `{user_id}`/`{claimed_user_id}` path segment participates in selection.
- **Resp model**: whether a Pydantic `response_model` is declared. `raw dict` means the
  handler returns an unvalidated `dict` (Part 33 gap).

---

## 1. Endpoint table (46 endpoints)

### Auth — `/auth/*`

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| POST | `/auth/login` | 133 | Email+password login, issues JWT | PUBLIC | n/a | `LoginRequest` | `LoginResponse` | `users` | — | no | `routes/auth.py` |
| POST | `/auth/signup` | 154 | Create account, returns session | PUBLIC | n/a | `SignupRequest` | `LoginResponse` | `users` | — | no | `routes/auth.py` |

### Chat & streaming — `/chat/*`

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| POST | `/chat/welcome` | 377 | AI-initiated opening turn (idempotent) | AUTHENTICATED | `assert_owner(body)` | `WelcomeRequest` | `ChatMessageResponse` | `messages`, graph, context | Bedrock | no | `routes/chat.py` |
| POST | `/chat/send` | 435 | Full LangGraph turn, JSON reply + bg extraction | AUTHENTICATED | `assert_owner(body)` | `ChatMessageRequest` | `ChatMessageResponse` | `messages`, graph, apm, patterns, +context | Bedrock | no | `routes/chat.py` |
| POST | `/chat/stream` | 483 | SSE token stream, same decisions as send | AUTHENTICATED | `assert_owner(body)` | `ChatMessageRequest` | SSE (`text/event-stream`) | `messages`, `action_card_logs`, +context | Bedrock | **yes** | `routes/streaming.py` |
| GET | `/chat/session/{user_id}/resume` | 535 | <500ms session hydration | AUTHENTICATED | `path` + `assert_owner` | query `session_id` | `SessionResumeResponse` | `messages`, `users` | — | no | `routes/chat.py` |

### Language & memory — `/api/language`, `/api/memory/*`, `/api/patterns/*`

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| POST | `/api/language` | 184 | Set `users.preferred_language` | AUTHENTICATED | `service(claimed)` | `LanguagePreferenceRequest` | raw dict | `users` | — | no | `routes/language.py` |
| POST | `/api/memory/consent` | 208 | Toggle personalization consent | AUTHENTICATED | token-only | `PersonalizationConsentRequest` | raw dict | `users` | — | no | `routes/memory.py` |
| POST | `/api/memory/feedback` | 219 | Record APM intervention feedback | AUTHENTICATED | `service(edge owner)` | `APMFeedbackRequest` | raw dict | `apm_edges`, `apm_events` | — | no | `routes/memory.py` |
| POST | `/api/patterns/feedback` | 243 | Confirm/deny a detected pattern | AUTHENTICATED | token-only | `PatternFeedbackRequest` | raw dict | `user_patterns` | — | no | `routes/patterns.py` |
| DELETE | `/api/memory` | 264 | Revoke derived memory (APM/patterns/facts/meditation) | AUTHENTICATED | token-only | — | raw dict | `apm_*`, `user_patterns`, `student_memories`, **`meditation_executions`/`meditation_offers` (direct)** | — | no | `routes/memory.py` |
| POST | `/api/memory/consolidate` | 291 | Rebuild profile summary from facts | AUTHENTICATED | token-only | — | raw dict | `users`, `student_memories` | — | no | `routes/memory.py` |

### Consultation — `/consultation-evaluation/*`

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| POST | `/consultation-evaluation/manual` | 302 | Evaluate self (or other via `target_user`) | AUTHENTICATED (staff for others) | `service(target)` | `ConsultationManualRequest` | raw dict | `psychiatric_evaluations`, `consultation_notifications`, `psychiatric_evaluation_audit` | — | no | `routes/consultation.py` |
| POST | `/consultation-evaluation/manual-override` | 318 | Force a decision, reason required | ADMIN (`is_staff`) | staff | `ConsultationOverrideRequest` | raw dict | same as above | — | no | `routes/consultation.py` |
| POST | `/consultation-evaluation/batch` | 337 | Evaluate many users | ADMIN (`is_staff`) | staff | `ConsultationBatchRequest` | raw dict | same as above | — | no | `routes/consultation.py` |
| GET | `/consultation-evaluation/status` | 360 | Signed-in user's latest decision | AUTHENTICATED | token-only | — | raw dict | `psychiatric_evaluations`, `consultation_notifications` | — | no | `routes/consultation.py` |

### Journal — `/journal/*`

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| POST | `/journal/entry` | 580 | Create entry | AUTHENTICATED | `service(claimed)` | `JournalEntryRequest` | raw dict | `journal_entries` | — | no | `routes/journal.py` |
| GET | `/journal/recent-entries` | 606 | Recent previews | AUTHENTICATED | token-only | — | raw dict | `journal_entries` | — | no | `routes/journal.py` |
| GET | `/journal/entry/{entry_id}` | 617 | Single entry (full) | AUTHENTICATED | token-only + entry lookup | path `entry_id` | raw dict | `journal_entries` | — | no | `routes/journal.py` |
| GET | `/journal/past-reflections` | 631 | Older previews | AUTHENTICATED | token-only | — | raw dict | `journal_entries` | — | no | `routes/journal.py` |
| GET | `/journal/calendar-data` | 642 | Calendar heatmap | AUTHENTICATED | token-only | — | raw dict | `journal_entries` | — | no | `routes/journal.py` |
| GET | `/journal/favorites` | 652 | Favorited previews | AUTHENTICATED | token-only | — | raw dict | `journal_entries` | — | no | `routes/journal.py` |
| GET | `/journal/stats` | 663 | Aggregate stats | AUTHENTICATED | token-only | — | raw dict | `journal_entries` | — | no | `routes/journal.py` |
| GET | `/journal/monthly-mindfulness` | 673 | Monthly rollup | AUTHENTICATED | token-only | — | raw dict | `journal_entries` | — | no | `routes/journal.py` |

### Sleep — `/api/sleep/*`

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| POST | `/api/sleep` | 683 | Log a night | AUTHENTICATED | `service(claimed)` | `SleepLogRequest` | raw dict | `sleep_logs` | — | no | `routes/sleep.py` |
| GET | `/api/sleep/recent` | 709 | Most recent night | AUTHENTICATED | token-only | — | raw dict | `sleep_logs` | — | no | `routes/sleep.py` |
| GET | `/api/sleep/history` | 720 | Last N days | AUTHENTICATED | token-only | query `days` (unbounded) | raw dict | `sleep_logs` | — | no | `routes/sleep.py` |

### Tracking (mood + habits) — `/api/mood/*`, `/api/habits/*`

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| POST | `/api/mood` | 732 | One mood check-in (idempotent) | AUTHENTICATED | `service(claimed)` | `MoodLogRequest` | raw dict | `mood_logs` | — | no | `routes/tracking.py` |
| GET | `/api/mood/recent` | 761 | Recent moods | AUTHENTICATED | token-only | query `days` | raw dict | `mood_logs` | — | no | `routes/tracking.py` |
| GET | `/api/habits` | 772 | List habits | AUTHENTICATED | token-only | query `include_archived` | raw dict | `habit_events` | — | no | `routes/tracking.py` |
| POST | `/api/habits` | 788 | Create habit | AUTHENTICATED | `service(claimed)` | `HabitCreateRequest` | raw dict | `habit_events` | — | no | `routes/tracking.py` |
| POST | `/api/habits/streaks` | 810 | Toggle streak visibility | AUTHENTICATED | token-only | `StreakVisibilityRequest` | raw dict | `users`, `habit_events` | — | no | `routes/tracking.py` |
| PATCH | `/api/habits/{habit_id}` | 822 | Rename/retime/pause/archive | AUTHENTICATED | `service(claimed)` | `HabitPatchRequest` | raw dict | `habit_events` | — | no | `routes/tracking.py` |
| POST | `/api/habits/{habit_id}/check-in` | 847 | Mark kept (idempotent) | AUTHENTICATED | `service(claimed)` | `HabitCheckInRequest` | raw dict | `habit_events` | — | no | `routes/tracking.py` |

### Tasks & reports — `/api/report_card/*`, `/api/reports/*`, `/api/session/*`

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| GET | `/api/report_card/tasks/{claimed_user_id}` | 879 | Today's tasks | AUTHENTICATED | `path→service(claimed)` | path `claimed_user_id` | raw dict | `daily_tasks` | — | no | `routes/tasks.py` |
| POST | `/api/report_card/tasks/complete` | 893 | Complete a task | AUTHENTICATED | `service(claimed)` | `TaskCompleteRequest` | raw dict | `daily_tasks` | — | no | `routes/tasks.py` |
| POST | `/api/report_card/tasks/custom` | 909 | Add custom task | AUTHENTICATED | `service(claimed)` | `TaskCustomRequest` | raw dict | `daily_tasks` | — | no | `routes/tasks.py` |
| PATCH | `/api/report_card/tasks/custom/{claimed_user_id}/{task_id}` | 929 | Edit/delete custom task | AUTHENTICATED | `path→service(claimed)` | `TaskCustomPatch` | raw dict | `daily_tasks` | — | no | `routes/tasks.py` |
| POST | `/api/reports/tasks/accept` | 953 | Accept a proposed report task | AUTHENTICATED | `assert_owner(body)+service` | `AcceptReportTaskRequest` | raw dict | `session_reports`, `daily_tasks` | — | no | `routes/tasks.py` |
| POST | `/api/session/report` | 976 | Generate post-session reading | AUTHENTICATED | token-only | `SessionReportRequest` | raw dict | `session_reports`, many context sources | Bedrock | no | `routes/reports.py` |

### Meditation — `/api/meditation/*`

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| POST | `/api/meditation/preview` | 993 | Dev preview of ranked practice | AUTHENTICATED | token-only | `MeditationPreviewRequest` | raw dict | `users`, `mood_logs`, `apm_*` | — | no | `routes/meditation.py` |
| POST | `/api/meditation/start` | 1005 | Begin execution | AUTHENTICATED | token-only | `MeditationStartRequest` | raw dict | `meditation_executions` | — | no | `routes/meditation.py` |
| POST | `/api/meditation/complete` | 1027 | Complete execution | AUTHENTICATED | token-only | `MeditationCompleteRequest` | raw dict | `meditation_executions` | — | no | `routes/meditation.py` |
| POST | `/api/meditation/feedback` | 1048 | HELPFUL/NOT_HELPFUL on execution | AUTHENTICATED | token-only | `MeditationFeedbackRequest` | raw dict | `meditation_executions` | — | no | `routes/meditation.py` |

### Health

| Method | Route | Line | Purpose | Auth | Ownership | Request | Resp model | Collections | Ext | Stream | Target |
|---|---|---|---|---|---|---|---|---|---|---|---|
| GET | `/health` | 1082 | Static liveness/readiness blob | PUBLIC | n/a | — | raw dict | none | — | no | `routes/health.py` |

---

## 2. Cross-cutting findings

### 2.1 No routers, no versioning, mixed prefixes
All 46 endpoints are declared directly on `app` in a single ~1090-line `app.py`. Prefixes
are inconsistent: `/auth/*`, `/api/*`, `/chat/*`, `/journal/*`, `/consultation-evaluation/*`,
bare `/health`. There is **no `/api/v1`**. Migration (Part 32) should mount the canonical
tree under `/api/v1` and keep the current paths as temporary compatibility aliases so
Streamlit, tests, and any voice client keep working.

### 2.2 Three different ownership patterns — the biggest correctness risk (Parts 10, 11)
- `assert_owner(body)`: `/chat/*` (welcome, send, stream, resume).
- `service(claimed)`: sleep, journal create, tasks, tracking writes, language — the domain
  layer runs `identity_keys`/`owns_claimed_id`.
- `token-only`: memory, patterns, meditation, journal reads, consultation status.
- `path`: `resume/{user_id}`, `report_card/tasks/{claimed_user_id}`, custom-task PATCH.

None of these are *broken* today (each path does verify ownership somewhere), but the
inconsistency is a latent hazard: a new route copied from the wrong template could trust a
client-supplied id. **Recommendation:** one shared dependency (`core/dependencies.py`)
that returns the authenticated id, plus a single `assert_owner` helper, applied uniformly.
Client-supplied `user_id` must never *select* a row — only ever be checked against the token.

### 2.3 Response models missing on 40/46 endpoints (Part 33)
Only `login`, `signup`, `welcome`, `send`, `resume` declare a `response_model`. Everything
else returns a raw `dict`. This blocks OpenAPI accuracy (Part 34) and contract tests
(Part 36). Response models can be added incrementally without changing behavior.

### 2.4 Direct Mongo in a route (Part 6)
`DELETE /api/memory` (lines 281–282) calls `db["meditation_executions"].delete_many(...)`
and `db["meditation_offers"].delete_many(...)` directly. This is the only route that
constructs Mongo access inline. It should move behind a `meditation` store function
(e.g. `purge_user_meditation(db, user_id)`), matching how the other deletes already
delegate to `delete_adaptive_memory`, `delete_user_patterns`, `delete_student_memory`.

### 2.5 No middleware at all (Parts 26–30, 39)
Grep for `add_middleware`, `CORSMiddleware`, `exception_handler`, `X-Request-ID`,
`RateLimit`, `slowapi` → **zero matches** in the whole repo. There is:
- no CORS / TrustedHost,
- no centralized exception handler (every route does its own `try/except` and several
  re-raise `HTTPException(status_code=500, detail=str(exc))`, **leaking raw exception text** —
  see `/chat/send`, `/chat/welcome`, `/chat/stream`, `/chat/.../resume`),
- no request-id middleware,
- no rate limiting,
- structured logging is absent (`logging.basicConfig` with a text format; no request_id,
  route, latency, or hashed user id fields).

### 2.6 Health is not real readiness (Part 29)
`/health` returns a static blob and never touches Mongo or Bedrock. There is no
`/health/live` vs `/health/ready` split. Readiness should check the Mongo ping and
(optionally) Bedrock config; liveness should stay dependency-free.

### 2.7 Streaming background-extraction ordering (Parts 14, 15)
`/chat/stream` schedules `run_background_extraction(..., reply="[Streamed Response]")`
*before* the stream runs, and the streaming generator separately persists the real turn.
The extraction therefore runs on a placeholder reply and fires even if the client
disconnects before the first token. Flag for Phase 10 (background jobs): extraction should
key off the persisted turn, and be idempotent so a disconnect/retry can't double-write
graph nodes / APM evidence.

### 2.8 Config, secrets, pool (Parts 7, 37)
- Mongo pool is **hardcoded** in `database.py` (`maxPoolSize=100, minPoolSize=5, …`), not
  env-driven. Part 7 wants `MONGO_MAX_POOL_SIZE`, `MONGO_MIN_POOL_SIZE`,
  `MONGO_SERVER_SELECTION_TIMEOUT_MS`, `MONGO_CONNECT_TIMEOUT_MS`, `MONGO_SOCKET_TIMEOUT_MS`.
- `ENCRYPTION_SECRET_KEY` and `AUTH_SIGNING_SECRET` have **insecure in-code defaults** and
  there is **no startup validation** that they were overridden in production.

### 2.9 Scheduler is not horizontally safe (Part 16)
`scheduler.py` is an 11-line stub: an in-process `CronTab` thread whose only job
`summarize_memories()` just `print(...)`s. Run with `--workers N` or multiple pods it would
fire N times. It is not wired into `app.py` today. Any real scheduled work must move to a
single dedicated worker/leader, not the API process.

### 2.10 `run.py` uses `reload=True` (Part 4)
Dev-only. Production start command must be documented (e.g.
`gunicorn app:app -k uvicorn.workers.UvicornWorker --workers N`) with **no** `--reload`.

### 2.11 Unbounded reads (Part 9)
`GET /api/sleep/history?days=` and `GET /api/mood/recent?days=` accept an unbounded `days`.
Journal read routes return service-defined counts but expose no caller limit. Define
`DEFAULT_LIMIT`/`MAX_LIMIT` and clamp.

---

## 3. Per-domain migration plan (route relocation only — Part 3)

The goal is to move **route declarations** into `api/routes/*` while the domain modules
(`sleep/`, `journaling/`, `tasks/`, `reports/`, `tracking/`, `consultation/`,
`student_memory/`, `meditation/`, `services/*`) stay exactly where they are and keep owning
business logic. Routes stay thin.

| New file | Absorbs | Backing domain/service (unchanged) |
|---|---|---|
| `api/routes/auth.py` | login, signup | `services/users.py` |
| `api/routes/chat.py` | welcome, send, resume | `services/graph.py`, `services/session_resume.py` |
| `api/routes/streaming.py` | stream | `services/streaming.py` |
| `api/routes/language.py` | `/api/language` | `services/language_preferences.py`, `services/users.py` |
| `api/routes/memory.py` | consent, feedback, delete, consolidate | `services/apm.py`, `services/patterns`, `student_memory` |
| `api/routes/patterns.py` | patterns feedback | `services/patterns` |
| `api/routes/consultation.py` | 4 consultation routes | `consultation/` |
| `api/routes/journal.py` | 8 journal routes | `journaling/service.py` |
| `api/routes/sleep.py` | 3 sleep routes | `sleep/reader.py`, `sleep/writer.py` |
| `api/routes/tracking.py` | 7 mood/habit routes | `tracking/mood.py`, `tracking/habits.py` |
| `api/routes/tasks.py` | 4 report_card + accept | `tasks/store.py`, `reports/store.py` |
| `api/routes/reports.py` | session/report | `services/session_report.py` |
| `api/routes/meditation.py` | 4 meditation routes | `services/meditation/service.py` |
| `api/routes/health.py` | health (+ new live/ready) | `db/mongo.py` ping |

`api/router.py` aggregates these with `include_router(..., prefix="/api/v1")`, and `app.py`
keeps thin compatibility shims so the current unversioned paths still resolve during the
deprecation window.

---

## 4. Collections discovered (25)

`users`, `messages`, `session_reports`, `journal_entries`, `daily_tasks`, `marks`,
`mood_logs`, `habit_events`, `action_card_logs`, `graph_nodes`, `graph_relationships`,
`apm_nodes`, `apm_edges`, `apm_events`, `user_patterns`, `pattern_evidence`,
`user_risk_turns`, `user_insights`, `meditation_executions`, `meditation_offers`,
`meditation_metadata_promotions`, `student_memories`, `psychiatric_evaluations`,
`consultation_notifications`, `psychiatric_evaluation_audit`.

Index ownership is already distributed across `*/indexes.py` modules and
`services/apm.py`, `services/mongo_graph.py`, `services/patterns/store.py`,
`services/chat_history.py`, `services/meditation/service.py`, all invoked from
`database.py`'s lifespan. The detailed index audit is Part 8 (`docs/database/indexes.md`),
a later phase.
