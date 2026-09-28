# Zenark encryption data matrix

**Status:** current-state contract (code is source of truth)  
**Date:** 2026-09-29

This is the authoritative inventory of what Zenark encrypts at rest. Other documents must not contradict it.

User content is encrypted in transit and at rest. The service decrypts content server-side when required for safety processing, conversational context, memory, insights, reporting, and approved model-provider calls. This is not end-to-end encryption.

---

## 1. Encryption architecture

| Item | Actual implementation |
|------|------------------------|
| Mechanism | Server-side Fernet (AES-128-CBC + HMAC) in `services/security.py` |
| Helpers | `encrypt_payload` / `decrypt_payload`; `seal_text` / `open_text` skip already-prefixed values |
| Key | PBKDF2-HMAC-SHA256, 100_000 iterations, 32-byte key, static salt `mental_health_salt_2026` |
| Secret | `ENCRYPTION_SECRET_KEY` (one key for all users) |
| Marker | Stored ciphertext is prefixed `enc::` |
| Key versioning | None |
| Who can read | The API process, anyone with the secret, and (for a turn) the configured model provider after server-side decrypt + optional PII redaction |
| Who cannot read from DB theft alone | An attacker with Mongo data and no `ENCRYPTION_SECRET_KEY` cannot open `enc::` fields |

Transit encryption is TLS at the deployment edge. This repository does not terminate TLS; it assumes HTTPS in staging/production.

---

## 2. Current encrypted fields

Exact `collection.field` values sealed before the Mongo write:

| Collection | Field | Writer | Primary readers | API exposure |
|------------|-------|--------|-----------------|--------------|
| `messages` | `content` | `services/chat_history.py` (`persist_message`, `update_welcome_message`) | chat history, session resume, streaming replay, graph idempotent retry, report transcript | Decrypted to the authenticated owner on chat/resume |
| `journal_entries` | `content` | `journaling/service.py` `create_journal_entry` | journal presenters, journal context, pattern adapters | Decrypted on owner journal GET; lists use a preview of decrypted text |
| `mood_logs` | `note` | `tracking/mood.py` `log_mood` (empty note is stored empty, not sealed) | mood presenters, `services/context.py` | Decrypted on owner mood API |
| `student_memories` | `fact` | `student_memory/store.py` `upsert_facts` | memory retrieve/context/consolidate | Not a public transcript API; decrypted into prompt/context |
| `session_reports` | `summary` | `services/session_report.py` `generate_session_report` | report context (`open_text`) | Decrypted for the owner on report/welcome paths |
| `session_reports` | `psychiatric_summary` | same | same | same |
| `user_insights` | `insight_summary` | `services/extraction.py` `_persist_extraction` | extraction persistence only | Not a student-facing field in the frozen API |
| `graph_nodes` | `name` | `services/mongo_graph.py` `upsert_node` | graph prompt formatting (`open_text`) | Not returned as a student transcript; used in prompts |

Fail-closed: encrypt success stores ciphertext; encrypt failure raises `CryptoIntegrityError` and does **not** write plaintext.

---

## 3. Current plaintext fields

### 3.1 User or model narrative still stored in the clear

These exist in code and are **not** passed through `seal_text` / `encrypt_payload`. They are documented gaps, not silent encryption work.

| Collection | Field | Notes |
|------------|-------|--------|
| `journal_entries` | `title` | Student-authored; body is sealed, title is not |
| `mood_logs` | `mood` | Short user-chosen label / emoji |
| `session_reports` | `events[].label` | Model-extracted situation labels |
| `session_reports` | `proposed_tasks[]` / `tasks[]` (`title`, `description`) | Copied later onto `daily_tasks` in the clear |
| `daily_tasks` | `tasks[].title`, `tasks[].description` | Student/report task text |
| `habit_events` | `title` | Student-authored habit name |
| `user_patterns` | `description`, `observations` | Derived pattern narrative |
| `pattern_evidence` | `value` | Feature payload; may be a string observation |
| `user_insights` | `detected_emotions`, `core_themes`, `suggested_habits` | Short labels from extraction JSON |
| `graph_nodes` | `properties` | Optional metadata blob; names are sealed, properties are not. Current graph extraction writes `{}`. |
| `graph_relationships` | `properties` | Structured edge metadata only: `intensity`, `source_session_id`, `source_message_id`, `status`. Free-form keys are dropped at write. |
| `student_psychological_profiles` | nested derived-safe fields (themes, conversation snapshots, `main_concern`, scores, …) | Consent-gated compact summary. Uses already-plaintext labels/tags/titles. Does **not** copy sealed report prose, raw chat, or `enc::` tokens. Not in the sealed-field set. |
| `dashboard_notifications` | `title`, `body` | School inbox; not student journal text |
| `dashboard_reports` | `body`, `spec` | School aggregate jobs |
| `psychiatric_evaluations` | `reasons`, `care_recommendation`, `signals` | Consultation rule output |
| `psychiatric_evaluation_audit` | `details` | Includes override reasons |
| `consultation_notifications` | (schema exists) | Collection is read for unread counts; current evaluate path does **not** insert rows (escalation is the notifier) |
| `meditation_executions` | `reason` | Optional short ranking reason |
| `action_card_logs` | `card` | Structured card JSON from a turn |

`users.memory_summary` and `users.key_takeaways` are **not written**. Prompt facts are reconstructed from sealed `student_memories.fact`. Leftover user fields are `$unset` on consolidate, `DELETE /api/memory`, and `POST /api/memory/erasure`.

### 3.2 Intentionally structured / non-content (plaintext is consistent)

Identifiers, clocks, scores, enums, and tenancy keys are stored in the clear on purpose.

| Collection | Examples |
|------------|----------|
| All student-owned rows | `user_id`, `session_id`, `*_id`, `created_at`, `updated_at`, `logged_at` |
| `messages` | `role`, `message_kind`, `seq`, `idempotency_key` |
| `mood_logs` | `score`, `input_format`, `crisis_flagged`, `client_event_id` |
| `sleep_logs` | `bedtime`, `wake_up_time`, `total_duration_minutes`, `date` |
| `session_reports` | `psychiatric_metric`, `crisis_signal`, `emotional_state` numbers, `withheld_reason` code |
| `habit_events` | `frequency`, `status`, `completions[]` dates, `reminder_time` |
| `meditation_executions` / `meditation_offers` | ids, `status`, durations, nonce, `pre_state` numbers |
| Dashboard | `school_key`, role audience, metric payloads |
| GDS / risk | `gds_snapshots`, `user_risk_turns` scores and class labels |
| Users | `email` (auth identifier), `crisis_flag`, consent booleans |

`sleep_logs` has no free-text note field in the writer.

---

## 4. Required future fields

Not implemented in this reconciliation. Do not treat this list as a silent schema change.

| Candidate | Classification | Why it is listed |
|-----------|----------------|------------------|
| `journal_entries.title` | Missing vs broad “user content” | Student-authored; only `content` is sealed |
| `session_reports.events[].label` and task titles | Deferred | Structured report side-channel; body summaries are sealed |
| `daily_tasks.tasks[]` text | Deferred | Product tasks are operational, not the journal body |
| `habit_events.title` | Deferred | Short label; not in the current sealed set |
| `graph_relationships.properties` | Intentional exception for frozen MVP | Node **names** are sealed; edges keep typed relation + sanitized structured metadata only |
| `user_patterns.description` | Deferred | Derived, not a student-typed body |
| `student_psychological_profiles` nested narrative | Deferred / derived-safe | Frozen contract: compact consent-gated summary, **not** a sealed collection. Spec does not require encrypting this document. |
| Per-user data keys / KMS | Explicitly out of scope | No key versioning in the frozen MVP |
| Client-held E2EE | Explicitly out of scope | Server must decrypt for safety, chat, memory, reports, models |

Product specification section 12’s **E2EE** wording is an architecture exception: the frozen MVP cannot be zero-access while the server screens crisis text and calls a model.

---

## 5. Decryption behavior

| Input | Result |
|-------|--------|
| Empty / missing | Returned unchanged |
| No `enc::` prefix | Returned unchanged (legacy plaintext rows) |
| Valid `enc::` + current key | Original plaintext |
| `enc::` + wrong or corrupt token | `CryptoIntegrityError("Decryption failed")` — **never** return the ciphertext to an API client |

JSON APIs map `CryptoIntegrityError` to the existing 500 envelope: `INTERNAL_ERROR` / “An unexpected error occurred.”

---

## 6. Encryption failure behavior

| Outcome | Behavior |
|---------|----------|
| Success | Persist `enc::…` ciphertext only |
| Failure | Raise `CryptoIntegrityError("Encryption failed")`, abort the write |
| Logs | `type=<ExceptionClass>` only — no plaintext, no ciphertext, no key |

Empty strings are a no-op (not a failure).

---

## 7. Key management

- One process-wide `ENCRYPTION_SECRET_KEY`.
- Static salt (changing it invalidates all `enc::` rows).
- Hardened `APP_ENV` (`production` / `staging` / `preprod`) refuses the placeholder secret at startup (`core/runtime_guard.py`).
- Production encrypt path also refuses the placeholder key.
- No dual-key reader in application code.

---

## 8. Production rotation constraints

See `docs/CREDENTIAL_ROTATION_RUNBOOK.md` §7.

Rotating the secret without an offline dual-key migrator makes existing `enc::` rows unreadable. The API then fail-closes (structured 500); it does not echo ciphertext. Staging may wipe data and mint a new key. Production must decrypt with the old key and re-encrypt with the new key before the API switches.

---

## 9. Model-provider boundary

- The **current user message** (and assembled prompt context, which may include decrypted history, journal previews, mood notes, memory facts, report readings, and graph names) is sent to Bedrock / configured fallbacks as **plaintext after server-side decrypt**.
- `anonymize_text()` redacts Indian mobiles, emails, and Aadhaar when `ENFORCE_PII_ANONYMIZATION` is on (`services/graph.py`, `services/streaming.py`, exam-buddy and dashboard assistant paths).
- Encryption at rest does **not** mean the provider receives ciphertext.
- There is no provider-side E2EE.

---

## 10. E2EE clarification

This system is **not** end-to-end encrypted, **not** zero-access encryption, and **not** a design where the operator or server cannot decrypt.

The server holds the key, opens content for safety and product behavior, and can send opened text to a model provider.

---

## 11. Erasure

`services/erasure.py` deletes owned rows with `delete_many({user_id})`. It does not decrypt first. Every collection that holds a sealed field is in `_OWNED` (`messages`, `journal_entries`, `mood_logs`, `student_memories`, `session_reports`, `user_insights`, `graph_nodes`).

Sealed ciphertext cannot remain because a decrypt failed: deletion is by owner id, not by opened content.

`POST /api/memory/erasure` also `$unset`s leftover `users.memory_summary` / `users.key_takeaways` for that user (no decrypt). Other users are untouched. Consultation/dashboard collections and `escalation_cases` unset-only remain separate erasure-coverage items, not decrypt-before-delete blockers.

---

## 13. Machine-readable contract identifiers

Tests parse the following lists. Do not edit them without updating `tests/test_encryption_contract.py`.

```
SEALED:
messages.content
journal_entries.content
mood_logs.note
student_memories.fact
session_reports.summary
session_reports.psychiatric_summary
user_insights.insight_summary
graph_nodes.name

PLAINTEXT_NARRATIVE:
journal_entries.title
mood_logs.mood
session_reports.events
daily_tasks.tasks
habit_events.title
user_patterns.description
graph_relationships.properties
student_psychological_profiles.conversations
```


---

## 12. Full audit matrix (requested domains)

| Data domain | Collection | Field | Writer | Reader | Encrypted? | Required by frozen at-rest set? | Exposed through API? |
|-------------|------------|-------|--------|--------|------------|----------------------------------|----------------------|
| Chat | `messages` | `content` | `chat_history` | resume, stream, graph, reports | Yes | Yes — implemented | Yes, decrypted to owner |
| Journal | `journal_entries` | `content` | `journaling/service` | journal routes, context | Yes | Yes — implemented | Yes, decrypted to owner |
| Journal | `journal_entries` | `title` | same | same | No | Not in current sealed set | Yes |
| Sleep | `sleep_logs` | times, duration, date | `sleep/writer` | sleep/context | No | N/A structured | Yes |
| Mood | `mood_logs` | `note` | `tracking/mood` | mood API, context | Yes (if non-empty) | Yes — implemented | Yes, decrypted |
| Mood | `mood_logs` | `mood`, `score` | same | same | No | Label/score N/A | Yes |
| Tasks | `daily_tasks` | `tasks[].title` | `tasks/store` | task API | No | Deferred | Yes |
| Habits | `habit_events` | `title` | `tracking/habits` | habit API | No | Deferred | Yes |
| Meditation | `meditation_executions` | ids, status, `reason` | `meditation/service` | meditation API | No | N/A / short reason deferred | Yes |
| Meditation | `meditation_offers` | nonce, `meditation_id`, `pre_state` | same | internal | No | N/A | No student transcript |
| Memory | `student_memories` | `fact` | `student_memory/store` | context, consolidate | Yes | Yes — implemented | Indirect (prompt/API memory helpers) |
| Patterns | `user_patterns` | `description` | `patterns/store` | pattern context | No | Deferred | Indirect |
| Patterns | `pattern_evidence` | `value` | same | detection | No | Deferred | No |
| Insights | `user_insights` | `insight_summary` | `extraction` | store | Yes | Yes — implemented | No student route |
| Reports | `session_reports` | `summary`, `psychiatric_summary` | `session_report` | welcome/report context | Yes | Yes — implemented | Yes, decrypted |
| Reports | `session_reports` | `events`, `proposed_tasks` | same | welcome, tasks | No | Deferred | Yes |
| Graph | `graph_nodes` | `name` | `mongo_graph` | prompt | Yes | Yes — implemented | Indirect |
| Graph | `graph_relationships` | `relation`, `properties` | `mongo_graph` | prompt | No (sanitized structured keys) | Intentional MVP exception | Indirect |
| Escalation | `escalation_cases` | `reason`, status | `escalation` | care | No | Enum/status N/A | Limited |
| Consultation | `consultation_notifications` | — | none currently | unread count | N/A | — | Count only |
| Consultation | `psychiatric_evaluations` | `reasons` | `consultation/evaluate` | status API | No | Deferred | Partial |
| Clinician briefs | *(none)* | Inner Council `consensus_brief` | in-memory | prompt only | N/A | Not persisted | No |
| Dashboard | `dashboard_reports` / `dashboard_notifications` | `body`, `title` | dashboard services | school staff | No | School aggregates, not student journal | Staff API |
| Memory copies | `users` | `memory_summary`, `key_takeaways` | none (unset on consolidate/erasure) | none | N/A — not stored | Closed derived-copy | No |
| Profile | `student_psychological_profiles` | derived-safe labels/scores | `student_profile` | chat context, owner API | No | Deferred derived store (not sealed) | Owner API (consent-gated) |
