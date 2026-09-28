# ZENARK PRE-PRODUCTION VALIDATION REPORT

Date: 2026-09-28 (Round 1) · 2026-09-29 (Round 2–5)  
Host: local Windows development machine  
Scope: frozen Core MVP (M1–M20 + P12). No product features added.  
This status is **not** clinical certification, legal compliance, production certification, or 150k-user capacity.

Round 2 updates are appended at the end. Earlier sections are the original Round 1 evidence and are not rewritten.

Current-state encryption contract (after fail-closed hardening and documentation reconciliation): [`ENCRYPTION_DATA_MATRIX.md`](ENCRYPTION_DATA_MATRIX.md). Round 1 §8 still correctly lists which fields were sealed; it does not describe fail-closed encrypt/decrypt (that landed later).

---

## 1. Baseline

Command:

```
python -m pytest tests exam_buddy_guardrails/tests -q --tb=line
```

| Metric | Result |
|--------|--------|
| Passed | **368** |
| Failed | **0** |
| Skipped | **0** |
| Errors | **0** |
| Duration | **5.80s** |

Frozen-verification baseline was 364. This run is 368 because this audit added four tests (three `runtime_guard`, one consultation same-school) without deleting or weakening existing tests. The erasure test was strengthened in place (same test count).

Expected frozen number 364 is therefore **not** the current engineering gate. Current green gate: **368 passed**.

---

## 2. Repository Hygiene

| Check | Result |
|-------|--------|
| `.env` currently tracked | No (`git ls-files` shows only `.env.example`) |
| `.env` in history | **Yes** — created in `10711e8` (Initial commit); later touched in `7253876`. File was subsequently removed from the tree. Treat every credential that ever lived in that file as **exposed**. Do not print values. **Rotate.** |
| `.env.example` tracked | Yes. Placeholders only (`YOUR_AWS_ACCESS_KEY_ID`, empty `SARVAM_API_KEY`, placeholder Fernet material). |
| Docker COPY of `.env` | `.dockerignore` excludes `.env` and `.env.*` except `.env.example`. |
| AWS access keys in current tree | No live `AKIA…` matches in source. Empty defaults in `config/config.py`. |
| Mongo URIs with credentials in current tree | Default is `mongodb://localhost:27017` (no password). |
| Hard-coded Sarvam key in current defaults | **Was present** in `config/config.py` (git history `config/config.py` at `c067dee4` and later). **Current default is empty.** History still contains the old value. **Rotate that key.** Value not printed. |
| Private certificates | No app certs tracked. Public CA bundles exist under tracked `.venv` (see below). |
| Secrets in tests | Dummy strings only (`super-secret-sarvam-key`, `SecurePass1`). |
| Secrets in logs / exception payloads | Access log hashes user ids; 500 handler strips detail (`tests/security/test_error_leakage.py`). |
| `.venv` tracked | **HIGH:** `git ls-files .venv` reports **17923** tracked files despite `.gitignore` listing `.venv`. Do not treat the GitHub tree as a clean source-only repo. |

No production credentials were rotated by this audit.

---

## 3. Environment Separation

There is **one** Settings object (`config/config.py`) parameterized by environment variables. There is **no** checked-in staging/production compose overlay. Separation is therefore an **operator contract**, not an enforced multi-file layout.

`APP_ENV` values treated as hardened: `production`, `prod`, `staging`, `preprod`.

Startup (`core/runtime_guard.py`, called from `api/application.py`):

- Hardened env refuses placeholder `ENCRYPTION_SECRET_KEY` and placeholder `AUTH_SIGNING_SECRET`.
- Hardened env disables `/docs`, `/redoc`, `/openapi.json`.
- Development still allows placeholders (local-only).
- `services/security.py` additionally refuses placeholder encryption when `APP_ENV` is `production`/`prod` at encrypt time.

Docker `Dockerfile` sets `APP_ENV=production`, so a container **will not start** until real encryption and auth secrets are injected. That is intentional.

**Collidable default:** `DATABASE_NAME=mental_health`. If staging and production share a cluster and an operator omits `DATABASE_NAME` / `MONGO_DB_NAME`, they share a database. Guard does not check this.

### Environment variable contract

| Variable | Required | Local | Staging | Production | Secret | Failure behavior |
|----------|----------|-------|---------|------------|--------|------------------|
| `APP_ENV` | Yes | `development` default | Must be `staging`/`preprod` | Must be `production`/`prod` | No | Wrong value: docs stay on; placeholder secrets accepted |
| `MONGODB_URI` / `MONGO_URI` | Yes to be ready | localhost default | Staging URI | Production URI | Yes if credentials | Process starts; `/health/ready` → 503 `mongo_unavailable` |
| `DATABASE_NAME` / `MONGO_DB_NAME` | Yes in shared clusters | `mental_health` | **Must differ** | **Must differ** | No | Silent data mix if URI shared |
| `ENCRYPTION_SECRET_KEY` | Yes outside local | Placeholder allowed | Unique, non-placeholder | Unique, non-placeholder | Yes | Hardened: process refuses to start |
| `AUTH_SIGNING_SECRET` | Yes outside local | Dev placeholder | Unique, non-placeholder | Unique, non-placeholder | Yes | Hardened: process refuses to start |
| `AWS_ACCESS_KEY_ID` | For Bedrock | Optional | Staging IAM | Production IAM | Yes | Warning; first LLM call fails |
| `AWS_SECRET_ACCESS_KEY` | For Bedrock | Optional | Staging IAM | Production IAM | Yes | Same |
| `AWS_REGION` | For Bedrock | `ap-south-1` | Env-specific | Env-specific | No | Wrong region → provider error |
| `BEDROCK_GEMMA_MODEL_ID` / `PRIMARY_MODEL` | For chat | Defaults | Staging model ids | Production model ids | No | Wrong model → provider error |
| `BEDROCK_SARVAM_MODEL_ID` | Fallback | Default | Staging | Production | No | Fallback fails |
| `SARVAM_API_KEY` | For STT/TTS | Empty default | Staging key | Production key | Yes | Voice returns structured provider error |
| `CORS_ORIGINS` | Browser clients | Empty = no CORS middleware | Staging origins | Production origins | No | Empty: browsers blocked (fail-closed) |
| `TRUSTED_HOSTS` | If used | Empty = off | Staging hosts | Production hosts | No | Empty: no Host header filter |
| `LOG_LEVEL` | No | `INFO` | `INFO`/`WARNING` | `INFO`/`WARNING` | No | Invalid → `INFO` |
| `RATE_LIMIT_*` | No | Process-local memory | Same unless Redis later | Same | No | Disabled if `RATE_LIMIT_ENABLED=false` |
| `AUTH_TOKEN_TTL_SECONDS` | No | 43200 | Env-specific | Env-specific | No | Longer TTL = stolen-token window |
| `CRISIS_HELPLINE_*` | No | India defaults | Confirm jurisdiction | Confirm jurisdiction | No | Wrong numbers in crisis card |

Do not hard-code production values. This audit did not.

---

## 4. Authentication & Authorization

Evidence: unit/API tests, route dependencies, call-graph inspection. **No live staging users.**

| Case | Expected | Evidence | Result |
|------|----------|----------|--------|
| Unauthenticated chat | 401 | `tests/api/test_http_contract.py` | PASS |
| User A chat as User B (`user_id` in body) | 403 | `test_cross_user_chat_is_403` + `assert_owner` | PASS |
| Student dashboard | 403 | `test_dashboard_requires_a_principal` | PASS |
| Teacher dashboard | 403 | same | PASS |
| Student evaluates another user (consultation) | PermissionError | `test_students_cannot_evaluate_others_but_staff_can` | PASS |
| Counselor School A evaluates School B student | PermissionError | `test_staff_cannot_evaluate_another_school` | PASS |
| `source=parent` consent grant | 403 `ACTOR_POLICY_PENDING` | `test_parent_actor_and_empty_assessment_catalog_stay_blocked` | PASS |
| Client `school_id` query | Ignored; tenant from auth user | `dashboard/dependencies.py`, overview test with Harbor `school_id` still returns Riverdale | PASS |
| Government → student API | No government product routes | Router inventory | N/A (not implemented) |
| Zone A → Zone B | No zone product | Router inventory | N/A |
| Clinician A → Clinician B booking/brief | No booking inventory product | Voice WS is session-scoped; cross-clinician IDOR **not** exercised live | NOT TESTED live |

Role checks are server-side (`authenticated_user_id`, dashboard `resolve_actor`, consultation `target_user`). No `parent=true` flag creates parent authority.

**Limitation:** staff **without** `tenant_key` may still name another user (`consultation/roles.py`). Legacy test `test_students_cannot_evaluate_others_but_staff_can` still allows `staff_1` → `stu_b`. Unassigned staff is a remaining IDOR-adjacent gap.

---

## 5. Tenant Isolation

Dashboard actor school comes from the signed-in user, never from a client school id.

| Adversarial case | HTTP | Evidence |
|------------------|------|----------|
| Harbor principal → Riverdale `stu_star` profile | 404 | `test_harbor_principal_cannot_see_riverdale_students` |
| Harbor principal → Riverdale intervention_id | 404 | `test_interventions_notifications_settings_and_reports` |
| Harbor principal → Riverdale teacher | 404 | teacher tests |
| Overview `school_id=Harbor` while Riverdale principal | Still Riverdale data | `test_overview_is_school_scoped_and_not_hardcoded` |
| Event bus School A vs B | Harbor queue empty | `test_events_stay_inside_one_school` |

School-facing queries use `school_key` on interventions, notifications, reports, settings, audit, teacher actions. Student profile requires `same_school` then loads marks by `student_id` (globally unique `user_id` assumed).

Student APIs are owner-scoped (`user_id` from JWT), not school-scoped.

Graph `$graphLookup` uses `restrictSearchWithMatch.user_id` (`services/mongo_graph.py`).

No zonal or government tenant plane exists to test.

---

## 6. Safety & EOS

Call graph (code + unit tests; **not** live Bedrock/Sarvam):

1. `POST /chat/send` → `run_chat_graph`. Crisis keyword (`contains_crisis_signal`) returns **before** LangGraph `ainvoke`, so **before** `fetch_context` (personalization/APM), **before** `retrieve_graph_context` (Graph RAG), **before** LLM.
2. `POST /chat/stream` → `stream_chat_graph`. Same pre-check before context/graph/LLM. Emits `crisis_alert` + helplines + `open_crisis_fast_track`.
3. Voice: `services/voice/service.py` STT → `run_chat_graph` (same gate). Tests mock `run_chat_graph`; live STT+crisis **NOT TESTED**.
4. Exam Buddy: `exam_buddy_guardrails/guardrails/classifier.py` calls shared `classify_message`; any non-`NONE` class is `UNSAFE`. `handle_exam_buddy_turn` opens EOS on `CRISIS_KEYWORD` and does not retrieve memories for unsafe turns.
5. Validator (`response_validator`) scores **assistant text**, not safety class. It cannot downgrade crisis because crisis never reaches generate on the keyword path.
6. Extraction skipped on crisis/incomplete (`services/extraction.py`).
7. EOS writer: `services/escalation.py` (`open_crisis_fast_track`, `on_turn`, `notify`). `notify` always `delivery=not_configured` / `notified=False` with no provider.
8. Dashboard `notify_counselor` writes an in-school notification, does not call EOS `notify`.
9. Consultation stores evaluations; no paging provider.
10. Action cards do not call EOS notify.
11. Unmapped GDS: `on_turn` returns without paging (`reason=band_unmapped`).

Crisis cannot be overridden by stored memory on the fast-track path: the graph is not invoked.

---

## 7. GDS

Code invariant: `resolve_care_band` is `UNMAPPED` unless a document is `APPROVED` **and** has `approved_by` **and** `approval_reference` **and** a mapping payload. A second distinct `APPROVED` version raises (`test_two_approved_mappings_are_rejected`).

This audit **did not** query a staging/production Mongo `gds_mapping_versions` collection (Mongo was down). Current **code + unit tests** contain no approved mapping and no student-facing `gds_value` (shadow stores `gds_value=None`).

Production/staging **database contents** for GDS: **NOT TESTED**.

No production GDS policy was created.

---

## 8. Privacy & Encryption

Protected fields sealed with `enc::` Fernet (`services/security.py`):

| Field | Module | Sealed at rest |
|-------|--------|----------------|
| Chat message content | `services/chat_history.py` | Yes |
| Journal body | `journaling/service.py` | Yes |
| Mood note | `tracking/mood.py` | Yes |
| Memory fact | `student_memory/store.py` | Yes |
| Session report summary | `services/session_report.py` | Yes |
| Insight summary | `services/extraction.py` | Yes |
| Graph node name | `services/mongo_graph.py` | Yes |

APIs return decrypted plaintext to the **authenticated owner** (or staff on owner-checked routes). Ciphertext is not the API contract.

Placeholder encryption: refused in production encrypt path and in hardened startup.

Live “plaintext not in Mongo” inspection: **NOT TESTED** (no Mongo). Unit round-trip: `test_encryption_round_trip_does_not_use_plaintext_prefix`.

Authorization-before-decrypt: chat/journal/mood routes authenticate then load by `user_id`. Dashboard student profile does not load journals.

---

## 9. Erasure

`POST /api/memory/erasure` (auth required) → `services.erasure.start_erasure` for **JWT user only**.

Unit evidence (`test_erasure_is_idempotent_and_user_scoped`, all `_OWNED` collections seeded):

| Collection | Deleted for target | Other user kept |
|------------|--------------------|-----------------|
| messages | 1 | keep |
| journal_entries | 1 | keep |
| sleep_logs | 1 | keep |
| mood_logs | 1 | keep |
| daily_tasks | 1 | keep |
| habit_events | 1 | keep |
| meditation_executions | 1 | keep |
| meditation_offers | 1 | keep |
| student_psychological_profiles | 1 | keep |
| student_memories | 1 | keep |
| user_patterns | 1 | keep |
| pattern_evidence | 1 | keep |
| user_insights | 1 | keep |
| session_reports | 1 | keep |
| apm_nodes / apm_edges / apm_events | 1 each | keep |
| graph_nodes / graph_relationships | 1 each | keep |
| exam_buddy_nodes / exam_buddy_relationships | 1 each | keep |
| gds_snapshots | 1 | keep |
| user_risk_turns | 1 | keep |
| escalation_cases | narrative/body/transcript **unset**; document **retained** | other narrative kept |

Job document: `job_id`, `user_id`, `status`, `counts`, `errors` (exception **type** only). Duplicate while queued/running returns same job; after success returns prior `job_id`; `rerun` after success returns zero counts.

**Not in `_OWNED` (still in DB after erasure):** `users`, `consent_grants`, `voice_sessions`, consultation evaluations/audit, `action_card_logs`, dashboard notifications/interventions referencing the student, `erasure_jobs` metadata.

**Live Mongo erasure:** NOT TESTED.

---

## 10. API Audit

Mounted routes: **148** (compatibility + `/api/v1` + dashboard-on-v1-only + docs in development).

Auth pattern: Bearer JWT via `authenticated_user_id` on student/clinical routes; dashboard via `dashboard_actor` (JWT + role + school). Health unauthenticated. Login/signup unauthenticated and rate-limited.

| Area | Auth | Owner/tenant | Notes |
|------|------|--------------|-------|
| `/health`, `/health/live` | None | n/a | Liveness |
| `/health/ready` | None | n/a | Mongo ping; no secrets |
| `/auth/login`, `/auth/signup` | None | n/a | 20/min process-local |
| `/chat/*` | JWT | `assert_owner` | Compatibility + v1 |
| `/journal/*` | JWT | owner | Encrypted body |
| `/api/memory/erasure` | JWT | self only | |
| `/api/v1/dashboard/*` | JWT + dashboard role + school | tenant | Not on unversioned tree |
| `/api/exam-buddy/ask` | JWT | self | Shared classifier |
| `/docs` `/redoc` `/openapi.json` | None | n/a | **Disabled when hardened** |
| WS `/ws/psychiatrist-voice` | Token on connect | session | Duplicate under `/api/v1` |

Compatibility routes retained (spec). No accidental admin CRUD. Dashboard `limit` capped 1–100. `ChatMessageRequest.message` has **no max_length** (limitation).

Rate limit: process-local; skips `/health*`, `/docs*`; identity = last 32 chars of `Authorization` or client IP.

---

## 11. Provider Failure

| Failure | Expected | Evidence | Result |
|---------|----------|----------|--------|
| Mongo down at startup | App continues; indexes warning | Observed uvicorn log; `/health/ready` 503 `mongo_unavailable` | PASS (this host) |
| Mongo down at runtime | Ready 503 | Locust 30/30 ready 503 | PASS |
| Bedrock primary | Fallback before first token (stream); structured failure after | `services/streaming.py` comments + `tests/test_fallback.py` (mocked) | PASS mocked; **NOT TESTED** live |
| Bedrock fallback | No stitch after first token | Streaming code | PASS code; NOT TESTED live |
| Sarvam STT/TTS | Structured error; key not echoed | `tests/test_voice.py` | PASS unit |
| Incomplete stream | Persist `incomplete`; no extraction | streaming + extraction guards | PASS unit |
| Safety vs provider | Crisis path does not call LLM | graph/stream fast-track | PASS code |

Safety gate is not bypassed by LLM failure on the keyword path.

---

## 12. Observability

| Signal | Present |
|--------|---------|
| `request_id` / `X-Request-ID` | Yes (`core/middleware.py`) |
| Access log method/route/status/latency/hashed user | Yes (`core/logging.py`) |
| Structured error envelope `success/error.code/message/request_id` | Yes |
| 500 sanitized | Yes |
| Crisis log | `logger.critical` with user_id + session_id (**not** message body) |
| Validator rejection | Used internally; client may get fallback/correction |
| EOS `case_id` | Stored on case doc; not verified in staging logs |
| Erasure job status | Returned on API |
| Message/journal/brief/prompt/completion in access logs | Not in access logger |

Sensitive data **can** enter `logger.exception` traces if an exception string contains payload. Unhandled handler logs method/path/request_id only.

Live log review of a staging cluster: **NOT TESTED**.

---

## 13. Staging

| Step | Result |
|------|--------|
| Separate staging env files in repo | None (operator `.env` only) |
| Staging Mongo | **Not available** on this host (`ServerSelectionTimeoutError`) |
| Docker engine | CLI present (28.4.0); **daemon not running** — `docker build` failed |
| Local API start | Uvicorn listened on `:8010` with `APP_ENV=development` |
| `/health/live` | 200 `{"status":"live"}` |
| `/health/ready` | 503 `mongo_unavailable` |
| Auth/chat/stream/voice/dashboard/erasure smoke | **NOT TESTED** (no DB, no providers) |
| Shutdown | Process stopped after locust |

Reproducible staging deployment **on this host: BLOCKED**.

Suggested operator sequence (not executed here):

1. Build: `docker build -t zenark-staging .` with Docker engine up; inject secrets via env, never bake `.env`.
2. Start with `APP_ENV=staging`, distinct `MONGO_URI`/`DATABASE_NAME`, real `ENCRYPTION_SECRET_KEY`/`AUTH_SIGNING_SECRET`, staging CORS.
3. `GET /health/live` then `GET /health/ready`.
4. Login, chat, stream, crisis keyword, erasure against **staging data only**.

---

## 14. Load Testing

**CURRENT limiter:** process-local memory. **PLANNED:** Redis. Redis was not implemented.

Locust 2.46.6 is installed. `load_tests/locustfile.py` had a defect (`def tasks` collided with Locust `tasks`); renamed to `list_tasks` (load-test only).

### A. In-process TestClient `GET /health/live` (ASGI, not network, not Mongo, not Bedrock)

Label clearly: **not** real capacity.

| Concurrent workers | Requests | Fail | RPS | p50 ms | p95 ms | p99 ms | avg ms |
|--------------------|----------|------|-----|--------|--------|--------|--------|
| 10 | 50 | 0 | 429.8 | 20.5 | 34.5 | 35.8 | 22.0 |
| 50 | 250 | 0 | 452.7 | 108.3 | 119.5 | 126.4 | 99.5 |
| 100 | 500 | 0 | 383.5 | 250.2 | 274.6 | 281.6 | 226.8 |
| 250 | 1250 | 0 | 381.9 | 599.9 | 709.0 | 732.0 | 570.6 |
| 500 | 2500 | 0 | 385.0 | 1288.7 | 1338.5 | 1396.6 | 1140.5 |

### B. Locust vs live `http://127.0.0.1:8010` — TEST A: 10 users, 20s

| Path | Reqs | Fails | Avg ms | p50 | p95 | RPS |
|------|------|-------|--------|-----|-----|-----|
| `/health/live` | 36 | 0% | 2 | 2 | 12 | 1.84 |
| `/health/ready` | 30 | **100% 503** | 4137 | 4100 | 4200 | 1.54 |
| Aggregate | 66 | **45.45%** | — | — | — | 3.38 |

TEST B–E (50/100/250/500 Locust users) **not run**: previous stage not stable (Mongo missing).

Chat, streaming first-token, dashboard aggregate, Exam Buddy, erasure job load: **NOT TESTED** (would hit real/mocked providers). No claim of 150k capacity. CPU/memory process samples: **NOT TESTED**.

---

## 15. Resilience

| Scenario | Result |
|----------|--------|
| API process start without Mongo | Starts; ready 503 | Observed |
| Index creation failure | Non-fatal warning | Observed (local pyOpenSSL noise + no server) |
| Restart API | Process killed after locust; no durability claim | Observed stop only |
| Duplicate crisis cases | `open_crisis_fast_track` returns existing open case `duplicate=True` | Code; NOT TESTED live |
| Duplicate stepping-stone nonce | Unit test `test_stepping_stone_outcomes_do_not_rewrite_each_other` | PASS unit |
| Duplicate erasure | Unit idempotency | PASS unit |
| Stream disconnect / incomplete extract | Unit streaming tests | PASS unit |
| Process restart during erasure job | Job is **synchronous in the request**; no queue | Limitation |
| Provider timeout live | NOT TESTED | |

`scheduler.py` is a daemon thread + crontab job stub (`summarize_memories` logs only). **Not** a distributed queue. Horizontal replicas would duplicate process-local rate limits, LLM circuit, scheduler, and erasure-in-request.

---

## 16. Database Performance

Indexes are declared in domain modules and `db/indexes.py` (users email/user_id unique; dashboard school_key compounds; messages; graph; APM; GDS; erasure jobs; escalation cases; etc.).

Important access patterns:

- `user_id` on owned collections
- `(school_key, intervention_id)` unique
- `(user_id, status)` on escalation cases and erasure jobs
- `$graphLookup` maxDepth from `GRAPH_TRAVERSAL_DEPTH` (default 2), `$limit: 30`, `restrictSearchWithMatch.user_id`
- History capped by `MAX_HISTORY_MESSAGES` (20)

**Explain plans / live collection scans:** NOT TESTED (Mongo down). No blind index rewrite.

Dashboard overview aggregates school users then risk turns for **severity labels** (not student-level journals). Student profile loads marks only.

---

## 17. Security Matrix

| Security Test | Result | Evidence | Severity |
|---------------|--------|----------|----------|
| Auth bypass on chat | PASS | 401 without Bearer | — |
| IDOR chat user_id | PASS | 403 | — |
| Tenant isolation dashboard | PASS | 404 cross-school student/intervention | — |
| Role escalation student→dashboard | PASS | 403 | — |
| Secret exposure current tree | PASS WITH LIMITATION | Defaults emptied; **history still has .env and old Sarvam default** | CRITICAL (history) |
| Log leakage of bodies | PASS (access logs) | Code review | — |
| Prompt injection / jailbreak class | PASS unit | `SafetyClass.JAILBREAK` | — |
| Unsafe action request | PASS unit | `test_action_boundary` | — |
| Cross-user memory/Graph/APM | PASS unit (owner filters + graph match) | Not live | — |
| School psychological leakage on student profile | PASS | Forbidden tokens in dashboard profile test | — |
| School aggregate risk labels | PASS WITH LIMITATION | Overview exposes school-level risk counts, not journals | Spec-level |
| Government identifier leakage | N/A | Feature absent | — |
| Parent authorization bypass | PASS | `source=parent` 403; no parent routes | — |
| Encrypted-field exposure | PASS unit | APIs decrypt for owner | — |
| Erasure bypass (other user) | PASS unit | Other user retained | — |
| Docs in production | PASS | Disabled when hardened | — |
| Consultation unassigned staff | FAIL / limitation | Staff without school can target another user | MEDIUM |

---

## 18. Readiness Scorecard

No numeric score.

| Area | Status | Why |
|------|--------|-----|
| A. Functional | PASS WITH LIMITATION | 368 unit tests green; live E2E chat/stream/dashboard **not** run |
| B. Security | PASS WITH LIMITATION | Auth/tenant unit tests pass; git history secrets; unassigned-staff consultation; `.venv` tracked |
| C. Privacy | PASS WITH LIMITATION | Encryption unit tests; live ciphertext-at-rest not inspected; erasure omits some collections |
| D. Reliability | PASS WITH LIMITATION | Ready correctly fails without Mongo; live provider failover not run |
| E. Performance | NOT TESTED | Only health/live baselines; no LLM/Mongo latency vs architecture targets |
| F. Observability | PASS WITH LIMITATION | request_id + envelopes in tests; staging log drain not reviewed |
| G. Deployment | BLOCKED | Docker daemon down; no staging Mongo; no image built this audit |
| H. Data lifecycle | PASS WITH LIMITATION | Erasure unit complete for `_OWNED`; live job + residual collections |
| I. External integrations | NOT TESTED | Bedrock/Sarvam not called |
| J. Scale | PASS WITH LIMITATION (honesty) | Process-local limiter/scheduler; 10-user locust failed ready; **not** 150k-ready |

---

## 19. Defects Found

### CRITICAL — Historical `.env` in Git

- **File/commits:** `.env` at `10711e8`, later `7253876`. Not currently tracked.
- **Root cause:** Initial commit included env file.
- **Fix:** Rotate every credential that ever lived there. Purge from history is an ops decision (filter-repo) after rotation.
- **Regression test:** `git ls-files -- .env` empty (current tree). History still fails.
- **Behavior changed:** No.

### CRITICAL — Hard-coded Sarvam default (current tree fixed; history remains)

- **File:** `config/config.py` (`SARVAM_API_KEY`).
- **Root cause:** Live-looking default shipped in settings.
- **Fix this audit:** default `""`. `tests/security/test_runtime_guard.py` asserts empty default.
- **Regression test:** `test_sarvam_default_is_empty`.
- **Behavior changed:** Yes — process with no env var no longer sends a baked-in key. **Rotate the old key.** Values not printed.

### HIGH — Placeholder secrets usable if `APP_ENV` is not hardened

- **Files:** `config/config.py`, `core/runtime_guard.py`, `services/security.py`.
- **Root cause:** Dev defaults.
- **Fix this audit:** startup refusal for production/staging/preprod; encrypt-time refusal for production.
- **Regression test:** `test_production_refuses_placeholder_auth_and_encryption`.
- **Behavior changed:** Staging/production containers fail closed without real secrets. Development unchanged.

### HIGH — `.venv` tracked (17923 files)

- **Root cause:** `.gitignore` lists `.venv` after files were added.
- **Fix:** Follow-up `git rm -r --cached .venv` (not done; huge tree rewrite).
- **Behavior changed:** No (not applied).

### HIGH — Staging dependencies missing on audit host

- Mongo not running; Docker engine not running.
- Not a product bug; **blocks** pre-production evidence.

### MEDIUM — Consultation staff without school can evaluate another user

- **File:** `consultation/roles.py`.
- **Fix this audit:** same-school required **when actor has tenant_key**.
- **Regression test:** `test_staff_cannot_evaluate_another_school`.
- **Behavior changed:** Yes for school-assigned staff only. Unassigned staff still allowed (legacy).

### MEDIUM — Locust file not executable

- **File:** `load_tests/locustfile.py` method named `tasks`.
- **Fix:** renamed to `list_tasks`.
- **Behavior changed:** Load-test tooling only.

### MEDIUM — Unbounded chat `message` field

- **File:** `schemas.py` `ChatMessageRequest`.
- **Fix:** Not applied (would change request validation behavior).
- **Follow-up.**

### MEDIUM — Default `DATABASE_NAME=mental_health`

- Collision risk across environments on one cluster.
- Not auto-fixed.

### LOW — Erasure is synchronous; cases retained; some collections not wiped

- Documented limitation vs full account deletion.

### LOW — `scheduler.py` is not a production distributed scheduler

- Honest scale limitation. Not converted.

---

## 20. Fixes Made

| Change | Product behavior |
|--------|------------------|
| Empty `SARVAM_API_KEY` default | Yes (safer default) |
| `assert_environment_secrets` + docs off in hardened env | Yes (startup/config only) |
| Consultation same-school when actor has tenant | Yes (closes cross-school staff eval) |
| Runtime-guard tests | Tests only |
| Erasure unit test covers all `_OWNED` collections + case unset | Tests only |
| Locust `list_tasks` rename | Load tests only |

No GDS mapping, parent access, government analytics, Redis limiter, or architecture redesign.

---

## 21. Remaining Limitations

- Rate limiting, LLM circuit, scheduler, erasure-in-request are **process-local**.
- Dockerfile `--workers 2` would split in-memory limiters.
- Chat payload size unbounded.
- Unassigned consultation staff can still target another user.
- Erasure is not a full GDPR-style account delete.
- Readiness does not validate Bedrock/Sarvam/encryption (encryption is startup-gated in hardened env).
- Compatibility routes and `/docs` in development remain.
- Historical secrets remain in git.
- `.venv` remains tracked.
- No 150k-user claim.

---

## 22. Required Follow-up Work

1. **Rotate** historical `.env` and former Sarvam default credentials (human/ops). Consider history purge after rotation.
2. Untrack `.venv`.
3. Provision **staging** Mongo with a **distinct database name**, staging IAM/Sarvam, unique encryption/JWT secrets.
4. Start Docker engine; build image; run smoke: login, chat, multilingual, stream, crisis, personalization on/off, stepping stone, Exam Buddy, dashboard IDOR, erasure against disposable users.
5. Repeat locust TEST A–E only when `/health/ready` is 200; keep chat/LLM scenarios mocked or quota-capped and labeled.
6. Capture Mongo `explain` on dashboard bundle + graphLookup.
7. Decide (product, not this audit) whether unassigned staff may evaluate others.
8. Optional: `max_length` on chat messages; Redis limiter when horizontally scaling.
9. Live log review for payload leakage.

---

## 23. Final Status

**NOT READY FOR PRE-PRODUCTION**

Gates that passed: unit suite green (368); auth/tenant unit matrix; crisis call graph; GDS unmapped invariant in code; erasure unit; health liveness vs readiness split; no current-tree baked Sarvam default.

Gates that did **not** pass this audit:

- Staging smoke (chat/stream/crisis/dashboard/erasure) against a real staging DB
- Reproducible container build (Docker daemon down)
- Live erasure collection evidence on Mongo
- Load tests with a ready dependency plane (TEST A locust 45% fail on ready; B–E not run)
- Measurable LLM first-token / Mongo p95 vs architecture targets
- Confirmation that staging/production Mongo contains **zero** APPROVED GDS rows
- Historical secret exposure still in git (rotation outstanding)

`PRE-PRODUCTION VALIDATED` is withheld until those gates are actually run — not because unit tests failed.

This does **not** mean clinically certified, legally compliant, production certified, or 150k-user ready.

Credential rotation details: `docs/CREDENTIAL_ROTATION_RUNBOOK.md`. External vendor rotation is **not** executed by this repository change.

---

# ROUND 2 — 2026-09-29

Previous limitation, action, evidence, current result.

## R2.1 Test baseline

| | Round 1 | Round 2 |
|--|---------|---------|
| Passed | 368 | **374** |
| Failed | 0 | **0** |
| Skipped | 0 | **0** |
| Errors | 0 | **0** |
| Duration | 5.80s | **5.82s** |

New tests: database-name guard (4), unassigned-staff consultation (1), chat message length (1). Existing tests were not weakened.

## R2.2 Credential rotation checklist (operator — not executed)

Do **not** print historical values. Treat every secret that ever lived in Git as compromised.

**History locations (no values):**

| What | Commits | Action |
|------|---------|--------|
| `.env` file | `10711e8`, `7253876` | Rotate **every** key that was in that file: AWS access/secret, Mongo URI passwords, Sarvam, encryption, JWT, any third-party token |
| `SARVAM_API_KEY` default in `config/config.py` | `c067dee4` | Disable/rotate that Sarvam key in the Sarvam console; current source default is empty |
| `AWS_SECRET_ACCESS_KEY` mentions | `c067dee4`, `7253876`, `08530466` | If those commits contained live keys (`.env` or otherwise), rotate IAM keys; create staging-only keys; never reuse in production |

**Operator steps:**

1. Inventory AWS IAM users/keys used by this repo. Deactivate keys that may have been in `.env`. Issue **new** keys into a secret store, not Git.
2. In Sarvam dashboard, revoke the historical API key. Create a staging key and a production key. Put them only in environment/secret manager.
3. If a Mongo URI with credentials was in `.env`, rotate the database user password and update only the secret store.
4. Generate new `ENCRYPTION_SECRET_KEY` and `AUTH_SIGNING_SECRET` for staging and production (different per environment). Old ciphertext cannot be read with a new encryption key — plan re-encryption or accept staging wipe.
5. After rotation, consider `git filter-repo` / BFG to purge `.env` blobs **after** keys are dead. Do not purge before rotation.
6. Confirm current tree: `SARVAM_API_KEY` default `""`; AWS defaults empty; `MONGODB_URI` default has no credentials. **Verified this round.**

**Rotation status:** checklist prepared. **External rotation not performed** (by design).

## R2.3 `.venv` tracking

| | |
|--|--|
| Previous | 17923 tracked files |
| Action | `git rm -r --cached .venv`; `.gitignore` now includes `.venv/` `venv/` `ENV/` |
| Evidence | `git ls-files .venv` → empty |
| Current | **PASS** (index untracked; local `.venv` left on disk). Deletions are staged; commit when ready. |

## R2.4 Database collision guard

| | |
|--|--|
| Previous | Default `DATABASE_NAME=mental_health` could be shared silently |
| Action | Hardened env requires `DATABASE_NAME` or `MONGO_DB_NAME` **in the process environment**. Staging/preprod **rejects** the shared default name `mental_health`. Production may set it explicitly. No staging database name is hard-coded. |
| Evidence | `tests/security/test_runtime_guard.py` |
| Current | **PASS** (code). Live staging start with this guard: **NOT TESTED** (no Mongo). |

## R2.5 Chat message bound

| | |
|--|--|
| Previous | Unbounded `ChatMessageRequest.message` |
| Action | `min_length=1`, `max_length=4000` (`MAX_CHAT_MESSAGE_CHARS`, same as Exam Buddy) |
| Evidence | `test_chat_message_length_is_validated` — empty/over → 400 `INVALID_REQUEST`; normal/at-limit → 200 |
| Current | **PASS** |

## R2.6 Unassigned-staff consultation

| | |
|--|--|
| Previous | Staff without school could name another user |
| Action | `target_user` requires `tenant_key` and `same_school` |
| Evidence | `test_unassigned_staff_cannot_evaluate_another_user`; same-school still allowed |
| Current | **PASS** |

## R2.7 Docker / staging / live gates

| Gate | Round 1 | Round 2 |
|------|---------|---------|
| Docker daemon | Down | **Still down** — `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.` CLI 28.4.0 present. **Build not faked.** |
| Local Mongo | Down | **Still down** — `ServerSelectionTimeoutError` |
| `/health/ready == 200` | No | **No** — smoke, live erasure, live safety, live provider, locust A–E, explain plans **not run** |
| Staging isolated DB | None | Still none on this host |

## R2.8 Remaining blockers

1. Operator **must rotate** historical credentials (checklist above).
2. Start Docker Desktop; build/run staging image with `APP_ENV=staging`, distinct `DATABASE_NAME`, non-placeholder secrets.
3. Start an isolated staging Mongo; `/health/ready` must be 200.
4. Then run 16 smoke checks, live erasure, live safety, labeled provider-failure, locust A–E, Mongo explain.

## R2.9 Round 2 final status

**NOT READY FOR PRE-PRODUCTION**

Code/security defects from Round 1 that this round could close in-repo are closed (venv tracking, DB name guard, chat bound, unassigned staff). Live staging, Docker image, Mongo, load, and credential **rotation** remain open. No 150k / clinical / legal / production certification.

## R2.10 Credential rotation follow-up

Full operator runbook: `docs/CREDENTIAL_ROTATION_RUNBOOK.md`.

External rotation is **not** executed in-repo. Until AWS / Sarvam / Mongo / Gemini / OpenAI / NVIDIA / Neo4j operators confirm revocation: **ROTATION PENDING — OPERATOR ACTION REQUIRED**. Git history was **not** rewritten.

---

# ROUND 3 — CREDENTIAL ROTATION / STAGING PREPARATION

Date: 2026-09-29  
Host: local Windows development machine  
Scope: operational execution of `docs/CREDENTIAL_ROTATION_RUNBOOK.md`. No product, GDS, safety, or encryption-architecture change. Production `ENCRYPTION_SECRET_KEY` was not swapped. Git history was not rewritten.

## R3.1 Historical credential inventory

Classified from Git objects without printing values.

| Type | Variable | Historical location | Rotation required |
|------|----------|---------------------|-------------------|
| MongoDB credentialed URI | `MONGODB_URI` | `.env` at `10711e8`, `08530466`; also `.env.example` at `10711e8` (live-shaped, not placeholder) | YES |
| Gemini API key | `GEMINI_API_KEY` | `.env` / `.env.example` at `10711e8`, `08530466` | YES |
| OpenAI API key | `OPENAI_API_KEY` | same | YES |
| NVIDIA API key | `NVIDIA_API_KEY` | same | YES |
| Neo4j URI | `NEO4J_URI` | same | YES if instance still exists |
| Neo4j password | `NEO4J_PASSWORD` | same | YES |
| Neo4j user | `NEO4J_USER` | same (non-empty) | VERIFY / disable user |
| Sarvam API key | `SARVAM_API_KEY` | `config/config.py` default at `c067dee4` (`api-key-shape`) | YES |
| AWS access key id | `AWS_ACCESS_KEY_ID` | tracked historical blobs: placeholder-or-empty (`c067dee4`, `7253876`, `08530466`) | VERIFY in IAM (no `AKIA` shape in classified Git) |
| AWS secret | `AWS_SECRET_ACCESS_KEY` | same | VERIFY in IAM |
| Encryption secret | `ENCRYPTION_SECRET_KEY` | public development placeholder in source / `.env.example` | Staging unique key; production dual-key migration only |
| JWT signing secret | `AUTH_SIGNING_SECRET` | public development placeholder | Unique staging; production cutover required |

Historical Mongo identity (no password recorded): user `zenark`, host `cluster0.30zvh8x.mongodb.net`.

`.env` is absent at `7253876` (file not in that tree). `.env` is present again at `08530466` with the same credential categories as `10711e8`.

## R3.2 Current repository safety

| Check | Result |
|-------|--------|
| `git ls-files -- .env` | empty |
| `git ls-files .venv` | empty |
| `SARVAM_API_KEY` default | `""` |
| AWS defaults | empty |
| Mongo default | `mongodb://localhost:27017` (no userinfo) |
| Encryption default | development placeholder (not a unique live key) |
| JWT default | development placeholder |
| Tracked secret-shape scan | no `AKIA`, no credentialed Mongo URI, no live `sk-` assignments |
| Docker COPY `.env` | still excluded by `.dockerignore` |

No new secrets were committed. Staging placeholders only in `.env.example` comments.

## R3.3 Runtime provider classification

| Provider | Runtime | Classification |
|----------|---------|----------------|
| AWS Bedrock | `llm_provider.py` ChatBedrockConverse | **ACTIVE** |
| Sarvam STT/TTS | `integrations/sarvam.py` / voice routes | **ACTIVE** |
| Gemini API | no runtime import | **LEGACY / UNUSED** — revoke historical key anyway |
| OpenAI API | no runtime import | **LEGACY / UNUSED** — revoke anyway |
| NVIDIA API | no runtime import | **LEGACY / UNUSED** — revoke anyway (still live; see below) |
| Neo4j | ignored driver args; one-off `scripts/migrate_neo4j_to_mongodb.py` | **LEGACY / UNUSED** — do not remove unrelated infra |

## R3.4 Credentials revoked

None confirmed at a vendor console in this round.

## R3.5 Credentials not found / host dead

| Item | Result |
|------|--------|
| Historical AWS `AKIA` in classified Git | **NOT FOUND** |
| Historical Mongo Atlas hostname | **NXDOMAIN** — `cluster0.30zvh8x.mongodb.net` DNS name does not exist |
| AWS CLI on this host | **NOT FOUND** |

## R3.6 Credentials not verified / still live

| Item | Probe (no values printed) | Status |
|------|---------------------------|--------|
| Historical Sarvam | STT HTTP **400** (not 401) | **NOT VERIFIED** — treat as possibly still authorized |
| Historical Gemini | HTTP **403** | **NOT VERIFIED** |
| Historical OpenAI | HTTP **401** | unauthorized observed; still confirm revoke in OpenAI dashboard |
| Historical NVIDIA | HTTP **200** | **STILL AUTHORIZED — REQUIRES OPERATOR** |
| Historical Neo4j | bolt not probed (driver not in app requirements) | **NOT VERIFIED** |
| Local gitignored Mongo (`cluster0.cuselcc.mongodb.net`, user `zenarkdevelopment_db_user`) | TLS/OpenSSL `AttributeError` on this interpreter | **NOT VERIFIED** |
| Operator AWS key suffix `S4FB` | STS OK; `ListAccessKeys` AccessDenied; **not deactivated** | **AWS ROTATION REQUIRES OPERATOR IDENTIFICATION** |

## R3.7 Staging credentials / DB / Docker

| Item | Status |
|------|--------|
| Staging secrets created in secret manager | **NOT COMPLETED** |
| Staging `DATABASE_NAME` ≠ `mental_health` live | **NOT COMPLETED** |
| Staging Mongo isolated | **NOT COMPLETED** |
| `docker build -t zenark-staging .` | **NOT RUN** — Docker daemon down: `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.` CLI present. **Not faked.** |
| `GET /health/live` | **NOT TESTED** |
| `GET /health/ready` | **NOT TESTED** |
| Production encryption key | **UNTOUCHED** — `PRODUCTION ENCRYPTION ROTATION = REQUIRES DUAL-KEY MIGRATION` |
| Production JWT | **UNTOUCHED** — `PRODUCTION JWT ROTATION = OPERATOR CUTOVER REQUIRED` |

## R3.8 Git-history purge

**NOT EXECUTED.** Historical NVIDIA credential still authorized. Purge remains: backup → confirm all old keys dead → `git filter-repo --invert-paths --path .env` → coordinated force-push. Not authorized in this task.

## R3.9 Remaining blockers

1. Revoke historical NVIDIA key immediately; then Sarvam, Gemini, OpenAI, Neo4j at vendor consoles.
2. IAM owner must inventory/rotate the operator AWS user; this agent will not deactivate an unmatched key.
3. Confirm Atlas user `zenark` / reused passwords are disabled even though the old hostname is gone.
4. Start Docker Desktop; inject unique staging env; use a distinct `DATABASE_NAME`.
5. `/health/ready` must be 200 on isolated staging before live pre-production / load tests.

## R3.10 Round 3 final status

**ROTATION PENDING — OPERATOR ACTION REQUIRED**

No 150k / clinical / legal / production certification. Do not proceed to load testing until the staging dependency plane is actually up.

---

# ROUND 4 — LIVE STAGING SMOKE + REAL INTEGRATION VALIDATION

Date: 2026-09-29  
Host: local Windows development machine  
Scope: live staging smoke against real Mongo, Bedrock, Sarvam, auth, memory/graph, streaming, voice, dashboard, erasure. **Stopped at preconditions.** No product behavior change. Live tests were **not faked**.

## R4.1 Precondition check

| Check | Result |
|-------|--------|
| Process `APP_ENV` | **UNSET** (not `staging`) |
| `GET /health/live` | **BLOCKED** — `URLError` on `127.0.0.1:8000`, `:8010`, `localhost:8000`, `:8010` (nothing listening) |
| `GET /health/ready` | **BLOCKED** — same; never reached Mongo ping |
| Docker daemon | **Down** — `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified.` |
| Listening 8000 / 8010 / 27017 | **None** |
| Local `mongod` | **Not installed** (`mongosh` present only) |
| Process staging secrets | Mongo / encryption / JWT / AWS / Sarvam **UNSET** |
| Local gitignored `.env` | `APP_ENV` unset; `DATABASE_NAME=mental_health` (**forbidden for staging**); encryption placeholder; JWT unset; Atlas host `cluster0.cuselcc.mongodb.net` |
| `ZENARK_BASE_URL` / `STAGING_BASE_URL` | **UNSET** |

**STOP rule applied:** `/health/ready != 200`. Did not start the API against `mental_health`. Did not treat local Atlas + placeholder encryption as staging. Did not use production credentials. Did not mock Bedrock/Sarvam as a substitute for this gate.

## R4.2 Tests not executed

All items below are **BLOCKED** (not FAIL of the application, not PASS of live integration):

Disposable fixtures, baseline health, real auth, IDOR, Mongo enc:: inspection, Bedrock `/chat/send`, `/chat/stream`, stream interrupt, crisis fast-track, safety taxonomy, language, personalization/memory, profile consolidation, Graph RAG, meditation/stepping stone, Exam Buddy, dashboard tenancy, EOS idempotency, erasure, secret leakage in live logs, controlled provider failure, PII anonymization against a live provider, Mongo `explain`, observability on live requests, data-integrity sweep, erasure cleanup.

## R4.3 Exact blockers

1. No Zenark API process is running on this host.
2. Docker Desktop engine is not running, so the staging image cannot be started here.
3. No isolated staging Mongo (`DATABASE_NAME` ≠ `mental_health`) is reachable on this host.
4. Process environment does not inject staging secrets (`APP_ENV=staging`, unique encryption/JWT, staging Mongo/Bedrock/Sarvam).
5. The only local dotenv points at database name `mental_health` with a placeholder encryption secret — using it would violate the staging contract.

## R4.4 Round 4 final status

**LIVE STAGING VALIDATION INCOMPLETE**

This is not a unit-test pass. Automated pytest was not used as a substitute for live staging. No clinical / legal / production / 150k claim.

---

# ROUND 5 — LOCAL STAGING ENVIRONMENT

Date: 2026-09-29  
Host: local Windows + Docker Desktop 28.4.0  
Scope: Docker + isolated Mongo + Zenark API + staging configuration. Product/GDS/safety/architecture unchanged. Local developer `.env` (`DATABASE_NAME=mental_health`) was **not** used.

## R5.1 Docker

| Check | Result |
|-------|--------|
| Initial daemon | Down; Docker Desktop was started for this round |
| `docker version` server | **28.4.0** |
| Compose | `docker compose --env-file .env.staging.local -f docker-compose.staging.yml` |

## R5.2 Build / containers

| Check | Result |
|-------|--------|
| Image | `zenark-staging` built successfully |
| Image excludes `.env` / `.env.staging.local` / `.git` / `.venv` | **PASS** |
| `mongo` | running, healthy, **no host port published** (internal `zenark-staging` network) |
| `api` | running, healthy, host `8000` |

## R5.3 Environment / isolation

| Check | Result |
|-------|--------|
| `APP_ENV` in API container | `staging` |
| `DATABASE_NAME` | `zenark_staging` (not `mental_health`) |
| Mongo hostname from API | `mongo` |
| Encryption / JWT in container | **SET**, not development placeholders |
| AWS / Sarvam in container | **UNSET** (not required for this plane) |
| App databases on this Mongo | `zenark_staging` only |
| `mental_health` on this Mongo | **absent** |

Secrets live only in gitignored `.env.staging.local`. `git ls-files -- .env.staging.local` empty.

## R5.4 Health / auth / encryption

| Check | Result |
|-------|--------|
| `GET /health/live` | **200** `live` (~14ms, request_id present) |
| `GET /health/ready` | **200** `ready` (~4ms, request_id present) |
| Signup + Bearer journal list | **201/200** |
| Invalid token | **401** |
| Missing token | **401** |
| Journal at rest | `content` **`enc::`**; synthetic plaintext **not** in Mongo field; API owner read decrypts |
| Journal title | plaintext (current contract) |
| Compose logs | staging password / encryption / JWT **absent**; no journal body |

## R5.5 Restart

`docker compose ... restart` → both services healthy; `/health/ready` **200**; users=1 journals=1 still in `zenark_staging`.

## R5.6 Tests / git

| Check | Result |
|-------|--------|
| pytest | **397 passed**, 0 failed, 0 errors, 0 skipped |
| `git ls-files -- .env` | empty |
| staging secret file tracked | **no** |

## R5.7 Remaining blockers (next gate, not this plane)

- AWS Bedrock and Sarvam are **not** injected into staging (intentional for this infrastructure gate).
- Round 4 **live provider smoke** is still pending.
- Do not use the developer `.env` (`mental_health`) as staging.

## R5.8 Round 5 final status

**STAGING DEPENDENCY PLANE READY**
