# ZENARK — ROUND 6 LIVE STAGING VALIDATION

Date: 2026-09-29  
Host: Shephin (Windows 10.0.26200)  
Git commit: `d2eca7a84d0b01dedf3cc4d71482ceacb01075a6` (`production-readiness/refactor`)  
Staging compose file: `docker-compose.staging.yml` (always invoked as `docker compose --env-file .env.staging.local -f docker-compose.staging.yml`)  
APP_ENV: `staging`  
Staging database: `zenark_staging`  
API endpoint: `http://127.0.0.1:8000`  
Containers: `zenark-staging-api` (image `zenark-staging`), `zenark-staging-mongo` (`mongo:7`)

This round validated the **existing** staging dependency plane. No product code was changed to make a test pass. Live Bedrock and live Sarvam **success** paths were not substituted with mocks.

Discovered routes actually called (not invented):

| Area | Route |
|---|---|
| Health | `GET /health/live`, `GET /health/ready` |
| Auth | `POST /auth/signup`, `POST /auth/login` |
| Chat | `POST /chat/send`, `POST /chat/stream`, `GET /chat/session/{user_id}/resume` |
| Journal / mood / sleep / habits / tasks | `POST /journal/entry`, `GET /journal/entry/{id}`, `POST /api/mood`, `POST /api/sleep`, `POST /api/habits`, `POST /api/report_card/tasks/custom`, `GET /api/report_card/tasks/{claimed_user_id}` |
| Language / memory | `POST /api/language`, `POST /api/memory/consent`, `POST /api/memory/consolidate`, `GET /api/memory/profile`, `POST /api/memory/erasure` |
| Meditation | `POST /api/meditation/preview`, `/start`, `/complete`, `/feedback` |
| Exam Buddy | `POST /api/exam-buddy/ask` |
| Voice | `POST /voice/stt` |
| Dashboard | `GET /api/v1/dashboard/context`, `/overview`, `/students/{id}/profile`, `/students/{id}/interventions` |

## Preconditions

| Check | Result | Evidence |
|---|---|---|
| API container running | PASS | `zenark-staging-api` Up, Docker healthcheck healthy, `0.0.0.0:8000->8000` |
| Mongo container running | PASS | `zenark-staging-mongo` Up healthy; host port unpublished |
| `APP_ENV=staging` | PASS | Container env `APP_ENV=staging` |
| Mongo hostname is staging service `mongo` | PASS | Runtime `MONGODB_URI` host token `mongo` (value not printed) |
| Database is `zenark_staging` | PASS | `DATABASE_NAME=zenark_staging`, `MONGO_DB_NAME=zenark_staging` |
| `mental_health` is NOT on staging Mongo | PASS | `list_database_names()` = `admin`, `config`, `local`, `zenark_staging` |
| Developer `.env` not used by staging | PASS | Compose `--env-file .env.staging.local`; hardcoded `DATABASE_NAME` / `APP_ENV` in compose `environment`. Host `.env` exists and was not passed to these commands |
| Bedrock credentials present | FAIL (absent) | `AWS_ACCESS_KEY_ID=UNSET`, `AWS_SECRET_ACCESS_KEY=UNSET`; `AWS_REGION=SET` |
| Sarvam credentials present | FAIL (absent) | `SARVAM_API_KEY=UNSET` |
| Encryption + JWT secrets present and not placeholders | PASS | `ENCRYPTION_SECRET_KEY=SET`, `AUTH_SIGNING_SECRET=SET` (not development placeholders) |
| Image/container secret-file hygiene | PASS | `/app/.env`, `/app/.env.staging.local`, `/app/.git`, `/app/.venv` all absent |

Bedrock-dependent LIVE success tests are **BLOCKED** (not mocked).  
Sarvam-dependent LIVE success tests are **BLOCKED** (not mocked).

## Test Results

| TEST_ID | Endpoint / Action | Expected Result | Actual Result | HTTP Status | Latency | Status | Evidence |
|---|---|---|---|---:|---:|---|---|
| R6-01 | `GET /health/live` + `GET /health/ready` | both 200; request IDs; ready confirms Mongo | live=200 `{"status":"live"}`; ready=200 `{"status":"ready"}` | 200/200 | 2.5ms / 4.3ms | PASS | `X-Request-ID` live=`e5117106e90142fd9ab7495b8664a1a0` ready=`381599c3b4bf4005b586a576c8b2dff3` |
| R6-02 | signup, login, authed journal list, missing/invalid token | signup+login succeed with JWT; authed 200; missing/invalid 401 | signup 201, login 200, JWT returned (not printed), authed 200, missing 401, invalid 401 | 201/200/200/401/401 | login 4.8ms | PASS | login `X-Request-ID=0f627e2ee7ef493f8af046fb1215a26f`; token never printed |
| R6-03 | User A vs User B journal/chat/language/tasks/exam-buddy | 401/403/404; body `user_id` cannot override | chat send as B 403; resume B 403; language as B 403; tasks B 403; exam-buddy `user_id` B 403; journal write as B 403; journal GET B's entry 404 | 403/404 | chat 5.0ms | PASS | `assert_owner` / claimed-id checks live; no cross-user journal body returned |
| R6-04 | Create journal+mood; inspect Mongo `enc::`; owner GET; other user GET | plaintext absent in protected field; owner decrypts; unauthorized denied | Mongo journal/mood/message content `enc::` prefix, synthetic marker absent in stored field; owner GET 200 decrypts; other user 404 | 200/404 | — | PASS | journal stored len=169 with `enc::`; mood note `enc::`; owner_decrypts=true; unauthorized_blocked=true. Bodies not recorded |
| R6-05 | LIVE Bedrock `POST /chat/send` (non-crisis) | actual Bedrock reply persisted | request executed; provider credentials missing; client 500 structured `INTERNAL_ERROR`; no mock intercept | 500 | 204.0ms | BLOCKED | Blocker: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` UNSET in API container. Logs: `NoCredentialsError` x6. Request ID `2accd574b5814888aaf0e60e19b4bc28`. Response leaks=[] |
| R6-06 | LIVE Bedrock `POST /chat/stream` | tokens, first-token latency, persist complete | non-crisis stream HTTP 200 with `event: error`, no `event: token` | 200 | 57.4ms | BLOCKED | Same AWS UNSET blocker. No first token. rid=`78a01ef3543843bab709312cee382095` |
| R6-07 | Interrupt live Bedrock stream | incomplete flag; no extraction on incomplete | not attempted against Bedrock (no stream tokens exist to interrupt) | — | — | BLOCKED | Blocker: cannot start a real Bedrock token stream. Incomplete-message count after failed pre-token stream = 0 |
| R6-08 | Crisis fixture `POST /chat/send` + `POST /chat/stream` | fast-track, helpline/card, no LLM/graph/APM | send 200 BOOKING_CARD + helpline; Hindi script after language=HINDI; stream `crisis_alert` then `done`, no `event: token` | 200/200 | 42.2ms / 38.8ms | PASS | rids `cec6bde3fa4b4f5a9935856f2e7b7044`, `66a148436f0146f8bb17dee40306b27c`. Mongo for crisis user: messages=4, graph_nodes=0, apm_*=0, user_risk_turns=0, escalation_cases=1. Logs: `Crisis keyword` x2, no fixture phrase in logs |
| R6-09 | Shared classifier via live Exam Buddy + crisis chat | taxonomy classes applied consistently | all 9 non-NONE samples → exam-buddy `UNSAFE` HTTP 200; NONE → `NON_ACADEMIC` 200; crisis chat CRISIS_KEYWORD fast-track | 200 | — | PASS | Labels only. Chat post-LLM classification for non-crisis classes was not live (would require Bedrock) |
| R6-10 | Preferred language; welcome; send vs stream; romanized; voice | selected language respected across those surfaces | `POST /api/language` HINDI 200; crisis send+stream used Hindi script. Welcome LLM, romanized contract, non-crisis send/stream, voice **not** live | 200 | 5.1ms | BLOCKED | Blocker: Bedrock/Sarvam required for welcome, next LLM reply, romanized script proof, and voice. Live subset only: preference write + crisis Hindi |
| R6-11 | Personalization OFF, then a retrieval-influenced turn | no personalized retrieval on the turn | consent OFF stored (`enabled=false`). After journal/mood/sleep/habit/task/consolidate: APM/graph counts=0. Non-crisis LLM turn not available | 200 | — | BLOCKED | Blocker: cannot prove retrieval-skipped LLM turn without Bedrock. DB evidence only: consent off, apm/graph empty |
| R6-12 | Personalization ON + prior context then new turn | permitted retrieval affects response, user-scoped | consent ON stored. Profile exists for that user only. No APM/graph facts created (no LLM extraction). Cross-user profile ids distinct | 200 | — | BLOCKED | Blocker: no live retrieval/use path through Bedrock. Do not treat profile field existence as personalization working |
| R6-13 | Profile/memory from supported sources | user-scoped derived profile; no raw sealed chat/journal | consolidate + `GET /api/memory/profile` 200 for consent-off and consent-on users; profile JSON has no `enc::`, no crisis fixture, no journal marker | 200 | 4.4ms | PASS | Source collections present per user. `conversations` / `journaling` keys are summaries/counts, not transcripts |
| R6-14 | Graph RAG create + retrieve | graph invoked, user-scoped, affects flow | no application-created graph facts (extraction needs LLM). Existence of `graph_nodes` collection is not evidence | — | — | BLOCKED | Blocker: Bedrock extraction/retrieval path unavailable |
| R6-15 | Meditation preview/start/complete/feedback; safety withhold | recommend when permitted; withhold on crisis; persist execution | non-crisis preview `NO_MEDITATION` (cold/weak estimate); crisis preview withheld_reason=`crisis`; start/complete/feedback 200 for catalog id `101` | 200 | 6.4ms | PASS | Execution persisted then later erased with user F. Chat stepping-stone card path not live (needs LLM) |
| R6-16 | Exam Buddy academic + follow-up + unsafe | academic works; unsafe shared-safety; ownership | unsafe live (R6-09). Academic `Explain the quadratic formula...` HTTP 500 `INTERNAL_ERROR`, leaks=[] | 500 | 959.2ms | BLOCKED | Blocker: academic path calls Bedrock. Unsafe path live-verified. IDOR 403 in R6-03 |
| R6-17 | Dashboard role + tenant isolation | staff role required; school from principal; no client `school_id` override; A cannot read B; student 403 | student context 403; two principals login 200; context A=`id:r6_school_a`, B=`id:r6_school_b`; overview A with `?school_id=r6_school_b` still `id:r6_school_a`; cross-school student profile 404; own student profile 200; cross interventions 404 | 200/403/404 | overview 4.5ms | PASS | Tenant comes from authenticated principal. Query `school_id` did not switch tenant |
| R6-18 | LIVE Sarvam STT | transcription from real provider | not executed as success. Missing key | — | — | BLOCKED | Blocker: `SARVAM_API_KEY` UNSET. Failure handling covered in R6-23 |
| R6-19 | LIVE STT → Zenark AI → TTS | valid audio round-trip | not executed | — | — | BLOCKED | Blocker: Sarvam UNSET and Bedrock UNSET |
| R6-20 | Erasure API + Mongo | owned collections deleted; other user kept; idempotent; APIs empty | clean target F: after-owned counts all 0; other user E retained journal/sleep/mood/tasks/habits/profile; second POST same `job_id`; account row remains (users not in erasure owned list) | 200/200 | — | PASS | Deleted: meditation_executions, profile, memories, patterns, APM, graph, exam-buddy graph, gds, risk turns. Escalation narrative unset. First dual-seed on user D left fixtures because a prior succeeded job short-circuits re-delete — that is the idempotency contract, not leftover from the clean F run |
| R6-21 | Live container logs | no secrets / raw sensitive payloads | 958 lines / 89k chars scanned | — | — | PASS | Hits=0 for AKIA, AWS secret assignment, Sarvam header, Bearer JWT, mongo URI, `enc::`, journal marker, disposable passwords, Authorization headers, crisis fixture phrase |
| R6-22 | Bedrock failure / fallback | fallback before first token; no stitch; no secret | mechanism = **existing missing staging AWS credentials** (no production/credential mutation). Primary fails before tokens; client 500 `INTERNAL_ERROR` + request id; fallback not observed (fallback is also Bedrock/Sarvam-on-Bedrock and also uncredentialed) | 500 | 204.0ms | BLOCKED | Blocker: no valid staging Bedrock identity to fail over *to*. Structured failure and no client secret leak were observed |
| R6-23 | Sarvam STT/TTS failure | structured error; no secret; no crash | `POST /voice/stt` with valid tiny WAV → 502 `PROVIDER_ERROR`; app remained healthy; request id present; leaks=[] | 502 | 52.6ms | PASS | Mechanism = missing `SARVAM_API_KEY`. TTS round-trip not separately failed (same blocker). rid=`2a38de08ae554e8f8969316a5794f5ff` |
| R6-24 | PII anonymization on live provider path | provider payload anonymized | not executed | — | — | BLOCKED | Blocker: live provider path unavailable. No real-person data used |
| R6-25 | Mongo `explain()` on critical queries | winning plan / index vs collection scan | all inspected finds used IXSCAN, no COLLSCAN | — | 0–2ms | PASS | See Mongo Explain section |
| R6-26 | Restart API; then Mongo | live/ready 200; persisted synthetic data remains | API restart: live 200 / ready 200 (14.4 / 3.6 ms); journal count=1; login 200. Mongo restart: live 200 / ready 200 (7.3 / 64.2 ms); journal count=1; login 200 | 200 | see actual | PASS | No duplicate-init corruption observed. Volume `zenark_staging_mongo_data` retained data |
| R6-27 | Data integrity sweep | staging-only DB; encryption; isolation; erasure; no production DB | databases still only `zenark_staging` (+admin/config/local); `mental_health` absent; encrypted_ok=10; plaintext protected-field hits=0; incomplete_messages=0; synthetic `@staging.zenark.test` users=14 of 15 | — | — | PASS | Crisis user isolated (graph/APM 0). User E data survived restart. Clean erasure F leftover owned=0. User D retains post-success seed fixtures because re-erasure is idempotent |

## Provider Validation

### AWS Bedrock

**BLOCKED** for LIVE SUCCESS. Not LIVE VERIFIED.

- Container: `AWS_ACCESS_KEY_ID=UNSET`, `AWS_SECRET_ACCESS_KEY=UNSET`.
- Non-crisis `/chat/send` and Exam Buddy academic turns reached the LLM client and failed with `NoCredentialsError` in container logs.
- That is evidence the runtime is the real Bedrock client, **not** a mock, and also evidence the call could not complete.
- Crisis `/chat/send` and `/chat/stream` intentionally never call Bedrock; those PASSes are not Bedrock verification.

### Sarvam STT

**BLOCKED** for LIVE SUCCESS. Failure path **LIVE VERIFIED** (R6-23).

- Container: `SARVAM_API_KEY=UNSET`.
- `/voice/stt` with a valid synthetic WAV returned structured 502 `PROVIDER_ERROR` without secrets.

### Sarvam TTS

**NOT TESTED** as a success path and **BLOCKED** as a live round-trip: STT never returned speech, and TTS sits behind the voice WebSocket plus Bedrock. No mock TTS was used.

## Security Findings

| Area | Result |
|---|---|
| Auth | Signup/login JWT issued. Missing and invalid bearer → 401. JWT not printed, not found in logs |
| IDOR | Cross-user chat, resume, language, tasks, exam-buddy body `user_id`, journal write → 403. Journal GET → 404. Body `user_id` did not retarget writes |
| Tenant isolation | Student cannot open dashboard (403). Principal A overview remained school A when `school_id=r6_school_b` was supplied. Cross-school student profile/interventions 404 |
| Secret leakage (HTTP) | Chat 500, exam academic 500, STT 502: leak pattern hits empty |
| Log leakage | No AWS keys, Sarvam headers, JWTs, mongo URIs, ciphertext, or journal marker in 45 minutes of API logs |
| Encryption at rest | Journal, mood note, and chat message protected fields used `enc::`. Owner API decrypted. Unauthorized GET 404 |
| PII handling | Live provider anonymization **BLOCKED**. Synthetic PII was not sent to a live LLM |

Observation (not a FAIL of executed contract): `/chat/send` maps unhandled provider errors through the generic 500 envelope (`INTERNAL_ERROR` / “An unexpected error occurred.”) even when the root cause is missing AWS credentials. That avoids leaking provider errors to the client.

## Data Integrity

- Staging DB: `zenark_staging` only. `mental_health` was never present on this Mongo.
- Encryption state after tests: 10 inspected protected fields `enc::`; 0 plaintext hits in `messages.content` / `journal_entries.content` sample.
- User isolation: A/B IDOR held; dashboard tenants A/B held; profile `user_id` matched token owner.
- Erasure: clean user F owned collections went to 0; user E retained source rows; users collection row kept (not in `_OWNED`); escalation narrative unset.
- Orphan note: user D still has APM/graph/memory fixtures inserted **after** a succeeded erasure job. Re-POST `/api/memory/erasure` returns the prior succeeded job and does not delete later inserts. That is the implemented idempotency contract. It is not a failed F erasure.
- Restart persistence: user E journal count remained 1 after API restart and after Mongo restart.
- Failed streams: `incomplete_messages=0` (failure was pre-token; nothing to mark incomplete).
- Crisis fast-track: crisis user messages persisted encrypted; graph/APM/risk-turn extraction did not run.

## Mongo Explain

Explain verbosity: `executionStats` via `db.command({explain: {find...}})`.

| Query | Winning plan | Index | COLLSCAN? | nReturned | docsExamined | keysExamined | ms |
|---|---|---|---|---:|---:|---:|---:|
| `users` `{user_id}` | FETCH ← IXSCAN | `user_id_1` | no | 1 | 1 | 1 | 2 |
| `users` `{school_id, isActive}` (dashboard-shaped) | FETCH ← IXSCAN | `users_school_id_active` | no | 3 | 3 | 3 | 0 |
| `messages` `{user_id, session_id exists}` sort `seq` | SORT ← FETCH ← IXSCAN | `session_user_created_seq` | no | 4 | 4 | 4 | 0 |
| `journal_entries` `{user_id}` sort `timestamp` | FETCH ← IXSCAN | `journal_user_timestamp` | no | 1 | 1 | 1 | 0 |
| `graph_nodes` `{user_id}` | FETCH ← IXSCAN | `uniq_user_node` | no | 0 | 0 | 0 | 0 |
| `student_psychological_profiles` `{user_id}` | FETCH ← IXSCAN | `uniq_student_psychological_profile_user` | no | 1 | 1 | 1 | 0 |
| `erasure_jobs` `{user_id}` | FETCH ← IXSCAN | `user_id_1_status_1` | no | 0 | 0 | 0 | 0 |

No index changes were made. These plans are from the live staging database after Round 6 fixtures, not a capacity study.

## Defects

### FAIL

None. No executed live test of in-scope application behavior was recorded as FAIL.

### BLOCKED

| TEST_ID | Exact blocker | Dependency required | Why it could not legitimately execute |
|---|---|---|---|
| R6-05 | `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` UNSET in `zenark-staging-api` | Staging-only AWS Bedrock credentials | Non-crisis `/chat/send` cannot complete a real Converse call. Not mocked |
| R6-06 | same | Staging-only Bedrock | No tokens emitted; cannot measure first-token or persistence of a completed LLM stream |
| R6-07 | same | Live Bedrock stream | Nothing to interrupt after first token |
| R6-10 | Bedrock + Sarvam UNSET | Staging Bedrock (welcome/send/stream) and Sarvam (voice) | Preference write + crisis Hindi were live; remaining language contract needs LLM/voice |
| R6-11 | Bedrock UNSET | Non-crisis authenticated chat turn | Consent OFF and empty APM/graph were observed; retrieval-skipped generation was not |
| R6-12 | Bedrock UNSET | LLM extraction + later retrieval turn | Consent ON stored; no graph/APM facts to retrieve |
| R6-14 | Bedrock UNSET | Extraction that writes `graph_nodes` then a retrieval turn | Direct Mongo inserts were used only as erasure fixtures, not as Graph RAG proof |
| R6-16 academic | Bedrock UNSET | Exam Buddy academic LLM | Unsafe taxonomy was live; quadratic-formula ask returned 500 |
| R6-18 | `SARVAM_API_KEY` UNSET | Staging Sarvam STT | Success transcription not possible |
| R6-19 | Sarvam UNSET + Bedrock UNSET | STT + chat + TTS | Voice round-trip not possible |
| R6-22 fallback | No valid staging Bedrock identity | Primary **and** fallback both need AWS | Missing-credential primary failure was observed; configured fallback success was not |
| R6-24 | Bedrock UNSET | Live provider-facing payload | Cannot inspect anonymized provider request without a real call |

### NOT TESTED

| Item | Why it was not attempted |
|---|---|
| Sarvam TTS success | STT never produced text; WebSocket voice turn also needs Bedrock. Failure-injection of TTS-only was not separately run after STT 502 already proved the speech client fail-closed |
| Destructive tests against non-synthetic data | Stop rule. Only `@staging.zenark.test` / `r6_fixture` principals were used |
| Production / `mental_health` connections | Stop rule. Staging Mongo has no such database |

## Performance data (descriptive only)

Not a load test. Not a 150k-user claim. Sample sizes are single-digit.

| Path | Observed |
|---|---|
| `/health/live` | 2.5–14.4 ms |
| `/health/ready` | 3.6–64.2 ms (higher after Mongo restart) |
| `/auth/login` | 4.8 ms |
| Crisis `/chat/send` | 42.2 ms (no LLM) |
| Crisis `/chat/stream` | 38.8 ms to complete SSE |
| Non-crisis `/chat/send` (credential fail) | 204.0 ms |
| Exam Buddy academic (credential fail) | 959.2 ms |
| `/voice/stt` failure | 52.6 ms |
| Dashboard overview | 4.5 ms |
| Mongo explains | 0–2 ms; IXSCAN |

No p50/p95 is reported; there were not enough repeated samples.

# ROUND 6 FINAL STATUS

LIVE STAGING VALIDATION INCOMPLETE

## Final summary

- Total PASS: **15**
- Total FAIL: **0**
- Total BLOCKED: **12**
- Total NOT TESTED: **0** (plus TTS success explicitly unused; counted under R6-19 BLOCKED)

**Critical blockers**

1. Staging API has no AWS Bedrock credentials (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` UNSET). Live `/chat/send`, `/chat/stream`, Exam Buddy academic, Graph RAG, personalization-influenced turns, PII-on-provider, and Bedrock fallback success cannot run.
2. Staging API has no Sarvam key (`SARVAM_API_KEY` UNSET). Live STT success and STT→LLM→TTS cannot run.

**High-severity defects**

None observed on the paths that actually executed.

**Exact next gate**

Inject **staging-only** Bedrock and Sarvam credentials into `.env.staging.local` (never the developer `.env`, never production keys), recreate or restart `zenark-staging-api` with `docker compose --env-file .env.staging.local -f docker-compose.staging.yml`, then re-run R6-05, R6-06, R6-07, R6-10 remainder, R6-11, R6-12, R6-14, R6-16 academic, R6-18, R6-19, R6-22 fallback, and R6-24.

After that provider re-run, the following gate is **controlled performance / load validation** of the staging plane. This report does not claim production readiness, clinical certification, legal compliance, 150k-user capacity, or complete security/scale.
