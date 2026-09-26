# API inventory

Source of truth: live route handlers in this repository as of the
`production-readiness/refactor` audit. Paths below are the **compatibility**
URLs that Streamlit and existing tests already call. The same handlers are
also mounted under `/api/v1` with the same path suffix (for example
`POST /chat/send` is also `POST /api/v1/chat/send`).

No invented endpoints are listed.

**Auth legend**

| Class | Meaning |
| --- | --- |
| PUBLIC | No Bearer token |
| AUTHENTICATED | Valid Bearer token required |
| STAFF | Authenticated **and** `users.role` in `{staff, admin, counselor, psychiatrist}` |
| OWNER | Authenticated identity is the only owner; a body/path `user_id` is checked, never used to select the target |

**Streaming:** only `POST /chat/stream` is SSE. Every other route is JSON.

**External services:** only the chat, welcome, session-report, and
meditation-preview paths call AWS Bedrock (primary Gemma, Sarvam fallback).
Background extraction after chat also calls Bedrock. Everything else is
MongoDB only.

**Caller:** Streamlit (`streamlit_app.py`) unless noted. Voice systems are
not in this repo.

---

## Compatibility vs versioned

| Status | Pattern |
| --- | --- |
| Canonical (new clients) | `/api/v1/...` |
| Compatibility (preserved) | existing paths below |
| Duplicate | none — the same handler is mounted twice |
| Legacy / to be removed | none yet; compatibility paths stay until clients migrate |

---

## Endpoints

### POST /auth/login

| Field | Value |
| --- | --- |
| Source | `api/routes/auth.py` |
| Prefix | none (also `/api/v1`) |
| Purpose | Email/password login |
| Auth | PUBLIC |
| Authorization | none |
| Request | `LoginRequest` `{email, password}` |
| Response | `LoginResponse` (token + public profile; password never returned) |
| Database | `users` |
| External | none |
| Streaming | no |
| Caller | Streamlit login |
| Duplicate/legacy | compatibility path |
| Migration | keep; prefer `/api/v1/auth/login` |

### POST /auth/signup

| Field | Value |
| --- | --- |
| Source | `api/routes/auth.py` |
| Prefix | none (also `/api/v1`) |
| Purpose | Create account; returns the same session as login |
| Auth | PUBLIC |
| Authorization | none |
| Request | `SignupRequest` `{email, password, name?}` |
| Response | `LoginResponse` (201) |
| Database | `users` |
| Errors | 409 if email exists |
| Streaming | no |
| Migration | keep; prefer `/api/v1/auth/signup` |

### POST /api/language

| Field | Value |
| --- | --- |
| Source | `api/routes/language.py` (`/language` + `/api` mount) |
| Purpose | Store `users.preferred_language` for the token owner |
| Auth | AUTHENTICATED + OWNER |
| Request | `LanguagePreferenceRequest` `{language, user_id?}` |
| Response | `{preferred_language, preferred_language_updated_at}` |
| Database | `users` |
| Notes | Body `user_id` cannot retarget. Canonical store is `users.preferred_language`. |

### POST /api/memory/consent

| Field | Value |
| --- | --- |
| Source | `api/routes/memory.py` |
| Purpose | Toggle `users.personalization_consent` |
| Auth | AUTHENTICATED |
| Request | `PersonalizationConsentRequest` `{enabled}` |
| Response | `{enabled}` |
| Database | `users` |

### POST /api/memory/feedback

| Field | Value |
| --- | --- |
| Source | `api/routes/memory.py` |
| Purpose | Explicit HELPFUL / NOT_HELPFUL on an APM intervention |
| Auth | AUTHENTICATED |
| Request | `APMFeedbackRequest` |
| Response | `{recorded}` |
| Database | `apm_events`, `apm_edges` |

### DELETE /api/memory

| Field | Value |
| --- | --- |
| Source | `api/routes/memory.py` |
| Purpose | Delete derived memory (APM, patterns, student facts, meditation executions/offers). Chat history and Graph RAG are retained. |
| Auth | AUTHENTICATED |
| Request | none |
| Response | deletion counts |
| Database | `apm_*`, `user_patterns`, `pattern_evidence`, `student_memories`, `meditation_executions`, `meditation_offers` |

### POST /api/memory/consolidate

| Field | Value |
| --- | --- |
| Source | `api/routes/memory.py` |
| Purpose | Rebuild `users.memory_summary` from stored facts |
| Auth | AUTHENTICATED |
| Database | `student_memories`, `users` |
| Also run by | `scripts/consolidate_memory.py` |

### POST /api/patterns/feedback

| Field | Value |
| --- | --- |
| Source | `api/routes/patterns.py` |
| Purpose | Confirm or disagree with a detected pattern |
| Auth | AUTHENTICATED |
| Request | `PatternFeedbackRequest` `{pattern_id, event_type, note?}` |
| Database | `user_patterns`, `pattern_evidence` |

### POST /consultation-evaluation/manual

| Field | Value |
| --- | --- |
| Source | `api/routes/consultation.py` |
| Purpose | Run `evaluate_user_for_consultation` for self, or another user if staff |
| Auth | AUTHENTICATED; STAFF if `payload.user_id` is someone else |
| Request | `ConsultationManualRequest` `{user_id?}` |
| Database | reports, patterns, risk turns, `psychiatric_evaluations`, notifications, audit |

### POST /consultation-evaluation/manual-override

| Field | Value |
| --- | --- |
| Source | `api/routes/consultation.py` |
| Purpose | Staff override of care status |
| Auth | STAFF |
| Request | `ConsultationOverrideRequest` `{user_id, status, reason}` |

### POST /consultation-evaluation/batch

| Field | Value |
| --- | --- |
| Source | `api/routes/consultation.py` |
| Purpose | Staff batch evaluation |
| Auth | STAFF |
| Request | `ConsultationBatchRequest` `{user_ids}` |

### GET /consultation-evaluation/status

| Field | Value |
| --- | --- |
| Source | `api/routes/consultation.py` |
| Purpose | Signed-in user's latest decision + unread notification count |
| Auth | AUTHENTICATED (self only) |
| Caller | Streamlit caption |

### POST /chat/welcome

| Field | Value |
| --- | --- |
| Source | `api/routes/chat.py` |
| Purpose | Idempotent AI opening turn; persists assistant only |
| Auth | AUTHENTICATED + OWNER (`payload.user_id`) |
| Request | `WelcomeRequest` `{user_id, session_id}` |
| Response | `ChatMessageResponse` |
| Database | `messages`, plus the full chat context stack |
| External | Bedrock (only when no welcome/messages exist) |
| Streaming | no |

### POST /chat/send

| Field | Value |
| --- | --- |
| Source | `api/routes/chat.py` |
| Purpose | Full LangGraph turn, JSON reply |
| Auth | AUTHENTICATED + OWNER |
| Request | `ChatMessageRequest` `{user_id, session_id, message}` |
| Response | `ChatMessageResponse` `{session_id, reply, action_cards}` |
| Database | messages + context collections |
| External | Bedrock |
| Background | `run_background_extraction` (in-process FastAPI BackgroundTasks) |
| Streaming | no |

### POST /chat/stream

| Field | Value |
| --- | --- |
| Source | `api/routes/streaming.py` |
| Purpose | Same decisions as `/chat/send`, SSE tokens |
| Auth | AUTHENTICATED + OWNER |
| Request | `ChatMessageRequest` |
| Response | `text/event-stream` |
| External | Bedrock |
| Streaming | yes |

### GET /chat/session/{user_id}/resume

| Field | Value |
| --- | --- |
| Source | `api/routes/chat.py` |
| Purpose | Last messages + dropped-session context |
| Auth | AUTHENTICATED + OWNER (path `user_id`) |
| Response | `SessionResumeResponse` |
| Database | `messages` |

### POST /journal/entry

| Field | Value |
| --- | --- |
| Source | `api/routes/journal.py` |
| Purpose | Create one journal entry |
| Auth | AUTHENTICATED + OWNER |
| Request | `JournalEntryRequest` |
| Database | `journal_entries` |

### GET /journal/recent-entries
### GET /journal/entry/{entry_id}
### GET /journal/past-reflections
### GET /journal/calendar-data
### GET /journal/favorites
### GET /journal/stats
### GET /journal/monthly-mindfulness

| Field | Value |
| --- | --- |
| Source | `api/routes/journal.py` |
| Auth | AUTHENTICATED (self) |
| Database | `journal_entries` |
| Notes | List endpoints return previews (`JOURNAL_PREVIEW_CHARS`), not full histories. |

### POST /api/sleep
### GET /api/sleep/recent
### GET /api/sleep/history

| Field | Value |
| --- | --- |
| Source | `api/routes/sleep.py` |
| Auth | AUTHENTICATED + OWNER on write |
| Request (POST) | `SleepLogRequest` |
| Database | `sleep_logs` |
| Query bound | history `days` clamped to `MAX_LIMIT` |

### POST /api/mood
### GET /api/mood/recent
### GET /api/habits
### POST /api/habits
### PATCH /api/habits/{habit_id}
### POST /api/habits/{habit_id}/check-in
### POST /api/habits/streaks

| Field | Value |
| --- | --- |
| Source | `api/routes/tracking.py` |
| Auth | AUTHENTICATED + OWNER |
| Database | `mood_logs`, `habit_events`, `users.show_streaks` |
| Notes | Mood `client_event_id` is idempotent. Habit check-in is idempotent per day. |

### GET /api/report_card/tasks/{claimed_user_id}
### POST /api/report_card/tasks/complete
### POST /api/report_card/tasks/custom
### PATCH /api/report_card/tasks/custom/{claimed_user_id}/{task_id}

| Field | Value |
| --- | --- |
| Source | `api/routes/tasks.py` |
| Auth | AUTHENTICATED + OWNER |
| Database | `daily_tasks` |

### POST /api/reports/tasks/accept

| Field | Value |
| --- | --- |
| Source | `api/routes/reports.py` |
| Purpose | Add one proposed report task to today |
| Auth | AUTHENTICATED + OWNER |
| Database | `session_reports`, `daily_tasks` |

### POST /api/session/report

| Field | Value |
| --- | --- |
| Source | `api/routes/reports.py` |
| Purpose | Post-conversation reading + optional meditation rank |
| Auth | AUTHENTICATED (self) |
| Request | `SessionReportRequest` `{session_id}` |
| External | Bedrock |
| Database | `session_reports`, messages, plus context collections |

### POST /api/meditation/preview
### POST /api/meditation/start
### POST /api/meditation/complete
### POST /api/meditation/feedback

| Field | Value |
| --- | --- |
| Source | `api/routes/meditation.py` |
| Auth | AUTHENTICATED (self) |
| Database | `meditation_executions`, `meditation_offers` |
| External | preview may call ranking (no new LLM path beyond existing engine) |
| Audio | metadata points at `meditation/meditation audios/`; API does not stream files |

### GET /health
### GET /health/live
### GET /health/ready

| Field | Value |
| --- | --- |
| Source | `api/routes/health.py` |
| Auth | PUBLIC |
| Purpose | `/health` and `/health/live` = process up. `/health/ready` = Mongo ping. |
| Secrets | none exposed |

---

## Count

45 compatibility application routes (the number `from app import app` reports,
excluding OpenAPI/docs), plus the same handlers remounted under `/api/v1`,
plus `/health/live` and `/health/ready`.
