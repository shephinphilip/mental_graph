# Production readiness report

Statuses below are honest. Completing a refactor is not the same as
being production-ready. **1,000,000 requests is a planning target, not
a measured capacity.**

## Existing architecture (before this work)

- One `app.py` held all HTTP routes.
- Domain packages (`sleep/`, `journaling/`, `tasks/`, `reports/`,
  `tracking/`, `consultation/`, `student_memory/`, `meditation/`,
  `services/`) already owned persistence and scoring.
- A partial `api/routes/` tree existed for auth/chat/memory/language but
  was not mounted. The live app still declared every route on `@app`.
- One Motor client in `database.lifespan` (good). Pool sizes were hardcoded.
- Streamlit called HTTP. `scheduler.py` is a stub in-process cron.

## Final architecture

```
HTTP → api/routes (thin) → domain/service → store/reader/writer → Mongo / Bedrock
```

`app.py` is a bootstrap: `create_app()` in `api/application.py`.
`python run.py` and `uvicorn app:app` still work.

Domain packages were **not** flattened into `services/`.

## Files moved / added / preserved

**Added:** `api/router.py`, remaining `api/routes/*`, `api/application.py`,
`api/presenters.py`, `core/*`, `db/mongo.py`, `db/indexes.py`,
`integrations/resilience.py`, `integrations/bedrock.py`, `workers/extraction.py`,
`Dockerfile`, `.dockerignore`, inventory/reference/scalability docs,
`load_tests/locustfile.py`, API/security tests.

**Preserved in place:** `sleep/`, `journaling/`, `tasks/`, `reports/`,
`consultation/`, `student_memory/`, `tracking/`, `meditation/`,
`services/` (LangGraph, APM, patterns, meditation engine, language resolver),
`config.py`, `schemas.py`, `llm_provider.py`, `prompts.py`, `streamlit_app.py`.

**Slimmed:** `app.py` (no remaining `@app` route declarations).

## APIs

- Discovered: 45 compatibility application routes (see `docs/api/API_INVENTORY.md`).
- Centralized: all of them, remounted under `/api/v1` without a second implementation.
- Deprecated: none. Compatibility paths stay until clients migrate.
- Streamlit still uses the old paths.

## Database / indexes

- Shared client/pool via `db.mongo.create_mongo_client`.
- Index orchestration via `db.indexes.ensure_all_indexes` calling existing
  domain `ensure_*` functions.
- No new collections. No second source of truth.

## Auth / security

- Bearer token still from `services.users`.
- Owner checks unchanged (`assert_owner`, `owns_claimed_id`, staff roles).
- 500 responses no longer echo `str(exc)` to clients.
- Request IDs on every response.
- Optional CORS / trusted hosts.
- Basic security headers.
- Process-local rate limits.

## LLM / streaming

- Existing Bedrock + Sarvam fallback preserved.
- `resilient_ainvoke` wraps graph + session-report `ainvoke` with timeout,
  concurrency cap, and (only when `APP_ENV` is production/staging) retry +
  circuit breaker.
- Streaming path still uses `get_primary_llm` / `get_fallback_llm` directly
  so first-token fallback behavior is unchanged.
- **PARTIALLY READY:** retries are env-gated so unit tests and local
  `APP_ENV=development` do not double-call mocks.

## Background jobs / scheduler

- Extraction still FastAPI `BackgroundTasks`. **NOT READY** for durable
  multi-instance production.
- `scheduler.py` still prints on a daemon thread. **Do not run it in
  every API replica.** There is no `crontab.txt` in the repo.

## Observability

- Access log: request_id, method, route, status, latency, hashed user id.
- Does not log bodies, tokens, or journal/chat text.
- Metrics backend (p50/p95 export, Mongo pool gauges): **NOT READY**.
- `/health`, `/health/live`, `/health/ready`: **READY**.

## Rate limiting

- **PARTIALLY READY.** Memory backend only. Multi-instance needs Redis
  (`RATE_LIMIT_BACKEND` is documented, not implemented).

## Docker

- Non-root, no `.env` / `.venv` / `.git` copied, health check, no `--reload`.
- Image not built in CI here. **NOT TESTED** in a registry.

## Load-test methodology / measured results

- Locust file: `load_tests/locustfile.py`.
- **Measured results: NOT TESTED.** No RPS, p95, or error-rate numbers
  were collected in this change. Do not claim 1,000,000 anything.

## Status board

| Area | Status |
| --- | --- |
| Route centralization | READY |
| Compatibility paths | READY |
| `/api/v1` aliases | READY |
| Domain logic preservation | READY |
| Shared Mongo pool | READY |
| Index documentation | READY |
| AuthN / owner checks | READY (existing) |
| Structured errors + request IDs | READY |
| Health live/ready | READY |
| Rate limiting (single process) | PARTIALLY READY |
| Distributed rate limiting | NOT READY |
| LLM timeout/concurrency | PARTIALLY READY |
| Streaming disconnect/heartbeat | PARTIALLY READY (pre-existing; not re-proven here) |
| Durable workers | NOT READY |
| Horizontal scheduler | NOT READY |
| Docker image | PARTIALLY READY (file exists, image not load-tested) |
| Metrics/tracing | NOT READY |
| Load test evidence | NOT TESTED |
| 1M-user capacity | NOT TESTED |
| Production cutover | NOT READY |

## Remaining production work

1. Run Locust (and Bedrock-backed chat) in staging; record p50/p95/p99.
2. Move extraction to a durable queue with the existing upsert idempotency.
3. Shared rate-limit store if more than one API replica.
4. Object storage + CDN for meditation audio.
5. Keep `scheduler.py` out of the API process.
6. Do not delete source journals/messages when deleting derived memory
   unless product explicitly requires it (current `DELETE /api/memory` already
   keeps chat + Graph RAG).
