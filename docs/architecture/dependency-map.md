# Zenark Dependency & Architecture Map — Phase 1 Audit

> Companion to [`docs/api/API_INVENTORY.md`](../api/API_INVENTORY.md). Read-only audit on
> branch `production-readiness/refactor`. Describes the system **as it is today**, so later
> phases can be judged against a fixed baseline.

## 1. Current layering (actual)

```
HTTP client (Streamlit / voice / tests)
        │
        ▼
app.py  ── 46 routes on a bare FastAPI() app, no routers
        │   auth dep: authenticated_user_id (Bearer → verify_access_token)
        │   ownership: _assert_owner OR claimed_user_id OR token-only (inconsistent)
        ▼
domain services & stores
        ├── services/            (chat graph, streaming, apm, patterns, context,
        │                         extraction, session_report, users, language, marks,
        │                         graph_rag, mongo_graph, inner_council, risk_assessor)
        ├── services/patterns/   (adapters, detect, score, window, store, retrieve, feedback)
        ├── services/meditation/ (service, engine, cards, demo_profiles)
        ├── sleep/ journaling/ tasks/ reports/ tracking/ consultation/ student_memory/
        └── meditation/          (data, metadata, audio)
        ▼
Motor AsyncIOMotorDatabase  (single shared client from database.py lifespan → app.state.db)
        ▼
MongoDB (25 collections)              AWS Bedrock (Gemma primary, Sarvam fallback)
```

The domain modularization is genuinely good and must be preserved (spec Parts 2, 20–25).
The problem is **not** the domain layer — it is everything *around* it: the HTTP layer is a
monolith, and there is no cross-cutting infrastructure (middleware, errors, request-id,
rate limiting, observability, resilience).

## 2. Entry points & process model

| Concern | Current state | Gap |
|---|---|---|
| App object | `app.py:app = FastAPI(lifespan=lifespan)` | single file, no `app/main.py`, no router tree |
| Dev launch | `run.py` → `uvicorn.run("app:app", reload=True)` | reload must be off in prod |
| Prod launch | documented only as a docstring in `run.py` | no Dockerfile, no gunicorn config committed |
| Startup | `database.py` lifespan: 1 Motor client, ensures ~13 index groups | pool sizes hardcoded, secret validation absent |
| Shutdown | `mongo_client.close()` | no graceful in-flight drain for SSE |
| Scheduler | `scheduler.py` in-process CronTab stub, **not imported by app** | not horizontally safe; not wired |

## 3. Shared infrastructure (what exists vs. what's missing)

| Capability | Exists? | Notes |
|---|---|---|
| Shared Mongo pool | ✅ | one client in lifespan, injected via `get_db(request)`; **no per-request client** — good |
| Env config | ✅ (partial) | `config.py` pydantic-settings; Mongo pool + several infra values not env-driven |
| Auth (authn) | ✅ | Bearer JWT via `services/users.py`; `authenticated_user_id` dependency |
| Authz (ownership) | ⚠️ | works but 3 inconsistent patterns (see inventory §2.2) |
| CORS / TrustedHost | ❌ | none |
| Centralized errors | ❌ | per-route try/except; several leak `str(exc)` at 500 |
| Request ID | ❌ | none generated or propagated |
| Structured logging | ❌ | `basicConfig` text format only |
| Rate limiting | ❌ | none anywhere |
| Health live/ready | ❌ | only a static `/health` |
| Metrics / tracing | ❌ | none |
| LLM resilience | ⚠️ | `with_fallbacks([sarvam])` + pre-first-token stream fallback; **no explicit timeout, retry/backoff, jitter, concurrency cap, or circuit breaker** |
| Caching | ⚠️ | only the language-preference runtime cache (correctly a cache, not source of truth) |

## 4. External dependencies

- **AWS Bedrock** via `langchain_aws.ChatBedrockConverse` (`llm_provider.py`). Primary
  `google.gemma-3-27b-it`, fallback pinned to a `sarvam.*` model. Region + keys from config
  (falls back to default AWS credential chain). Called from `services/graph.py` (send) and
  `services/streaming.py` (stream). No direct Bedrock calls from route files. ✅
- **MongoDB** via Motor. Single async client. ✅
- **LangGraph** pipeline lives in `services/graph.py` (`run_chat_graph`) — orchestration is
  already separate from routes and from domain stores. ✅ Preserve (Part 13).

## 5. Coupling / hotspots to watch during refactor

| File | Role | Risk if moved carelessly |
|---|---|---|
| `services/context.py` `fetch_user_context` | central fan-in: language, reports, patterns, sleep, journal, tasks, memory, care | both chat paths depend on identical keys; keep the two call sites in lockstep |
| `services/streaming.py` | rebuilds the full system prompt itself (mirrors `graph.py`) | prompt keys duplicated across send/stream — a known drift risk |
| `services/graph.py` + `services/streaming.py` | both call `format_system_prompt` | changes must land in both |
| `database.py` lifespan | imports ~13 `ensure_*_indexes` | central index bootstrap; preserve domain ownership when centralizing (Part 8) |
| `tasks/identity.py` (`identity_keys`, `owns_claimed_id`) | ownership primitive reused by sleep/journal/tracking/tasks | the de-facto identity layer already; a good seed for `core/dependencies.py` |

## 6. Baseline test coverage

`246 passing` at audit time (`pytest tests/`). Tests are flat under `tests/` with
hand-rolled Mongo doubles per file (no `conftest.py`, no `pytest.ini`). Notably, most tests
call **service functions directly**, not the HTTP layer — which is exactly why the tracking
routes could be missing for a while without a red suite (fixed earlier). Phase 13 should add
`tests/api/` contract tests that exercise the mounted routes through `TestClient`.

## 7. Recommended phase order (matches spec Part 45)

1. **Phase 1 — Audit (this doc + inventory).** ✅ done.
2. **Phase 2 — `api/` tree + central router**, mount under `/api/v1`, keep old paths as
   compatibility aliases. Move *route declarations only*.
3. **Phase 3 — thin the routes**: pull the direct Mongo out of `DELETE /api/memory`; add
   response models.
4. **Phase 4 — `core/` cross-cutting**: request-id middleware, centralized error handler
   (no leaked internals), structured logging, `core/dependencies.py` unifying ownership.
5. **Phase 5 — `db/mongo.py`**: env-driven pool + `/health/live` + `/health/ready`.
6. **Phase 6 — authz hardening** + cross-user isolation tests across every domain.
7. **Phase 7 — LLM resilience** (timeouts, bounded retry+jitter, concurrency cap).
8. **Phase 8 — background jobs / scheduler** (durable, idempotent, single-runner).
9. **Phase 9 — rate limiting + Docker + prod start command.**
10. **Phase 10 — API_REFERENCE.md + openapi.yaml + contract tests.**
11. **Phase 11 — load tests + `scalability.md` + measured results.**

Run `pytest` after **every** phase. No phase claims production readiness on its own;
`docs/PRODUCTION_READINESS_REPORT.md` (Part 48) is written last with honest
READY/PARTIALLY READY/NOT READY/NOT TESTED statuses.
```
