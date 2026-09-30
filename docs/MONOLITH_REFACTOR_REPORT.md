# Zenark Monolith Refactor Report

## Before

One FastAPI process in `C:\company\chat\mental_health` mounted mental health, Exam Buddy, platform auth, and the school dashboard. Commit `b323039fb7e3b905f317a47e44dab75b074af674` on `production-readiness/refactor`. Working tree was clean. `python -m pytest tests exam_buddy_guardrails/tests -q` reported **458 passed**, 0 failed, 0 skipped, 2 warnings. The live router had **154** method/path pairs.

## After

Three application directories in the same git repository:

- `backend-agent` — mental health and Exam Buddy
- `backend-core` — shared platform and the Bedrock client
- `dashboard` — school dashboard

The git root was not moved. `C:\company\chat\zenark_repo` is a sibling folder. Junctions there point at these three directories so that path and the git tree are the same files.

## File Migration Summary

Git recorded about 433 renames, plus new entrypoints, Dockerfiles, and docs. Moves used `git mv`. History was not rewritten.

backend-agent received the API, chat, voice, safety, APM, graph, EOS, consultation, journal, meditation, sleep, tracking, tasks, reports, student memory, workers, prompts, schemas, and Exam Buddy.

backend-core received `config`, `core`, `database`, `db`, encryption, users, mark reads, school tenancy, and `llm_provider`.

dashboard received the existing dashboard package, now nested as `dashboard/dashboard`, plus `dashboard_app.py`.

## Route Ownership

Before: 154 routes on one application.

After: 0 missing routes. backend-agent has 114 routes and no `/api/v1/dashboard` path. The dashboard application has the 40 dashboard routes. Overlap is only `/health/live`, `/health/ready`, and the development docs routes, and those dashboard copies are process probes. The public gateway sends `/health` to backend-agent.

See `docs/API_OWNERSHIP_MATRIX.md`.

## Dependency Changes

backend-agent and the dashboard import backend-core. They do not import each other.

`set_preferred_language` moved from the user module into `services/language_preferences.py` so the platform package does not import the language cache. Callers use the same function.

School tenant helpers used by consultation and the dashboard live once in `backend_core.tenancy`. The dashboard re-exports them.

`llm_provider` moved to backend-core. The import name is unchanged. The dashboard assistant still calls `get_llm()`.

## Database/Collection Ownership

One Mongo database. Ownership is documented in `docs/REPOSITORY_ARCHITECTURE.md`. The dashboard still reads mood, sleep, pattern, risk, meditation, and profile-attendance documents for the existing wellbeing aggregate. It does not write those collections and it does not import the services that own them.

Index creation is split. The agent process ensures mental-health and Exam Buddy indexes. The dashboard process ensures dashboard indexes. User indexes are ensured by the shared database lifespan. `create_index` stays idempotent.

## Shared Infrastructure

Encryption, JWT verification, request IDs, error envelopes, rate limiting, the Mongo client, and settings have one implementation in backend-core.

## Docker

`backend-agent/Dockerfile`, `backend-core/Dockerfile`, and `dashboard/Dockerfile` build from the repository root. The root `Dockerfile` matches the agent image.

`docker-compose.staging.yml` runs `api`, `core`, `dashboard`, `gateway`, and `mongo`. Mongo is not published. Host port 8000 is the gateway. The API container name remains `zenark-staging-api`.

Independent `python run.py` was checked for all three applications. Agent `/health` returned the historical payload. `POST /chat/send` on port 8000 returned 401. `GET /api/v1/dashboard/overview` on the agent returned 404 and on the dashboard returned 401.

The Docker engine was not running (`dockerDesktopLinuxEngine` pipe missing), so image builds and a live staging boot were not executed. Docker and staging are BLOCKED, not passed.

## Configuration

Secret files stayed at the repo root and were not committed. Settings still load that root `.env`. The settings schema is still one object so staging secret checks and defaults do not change.

## Tests

Baseline: 458 passed.

After the move: **462 passed**, 0 failed, 0 skipped. The four added tests check import boundaries and that `/chat/send` and `/api/v1/dashboard/overview` have different owners.

After the standalone packaging follow-up: **463 passed**, 0 failed, 0 skipped. The extra test keeps the welcome-prompt assertion in backend-agent so backend-core tests do not import `prompts`.

## Git

Branch `production-readiness/refactor`. The checkpoint commit is `3f9670502b5cbcc3aab6324ddac4711b1355a53d`, parent `b323039fb7e3b905f317a47e44dab75b074af674`, message `refactor: split zenark applications into agent core and dashboard`. History was not rewritten. Nothing was force-pushed.

Published tips:

- backend-core `4e2798aa4c88f43fefef0288abd1af0d57311ff7` on https://github.com/Zenark-2025/backend-core `main`
- backend-agent `a3e9e6540a81fdb4867b91042acd25bdea22acfe` on https://github.com/Zenark-2025/backend-agent `main`

backend-agent pins `zenark-backend-core` at `4e2798aa4c88f43fefef0288abd1af0d57311ff7`. The dashboard was not published.

## Compatibility

- `python run.py` and `uvicorn app:app` from the repository root use `app.py`, which mounts the dashboard router on the agent app. That keeps a single local process.
- Staging does not use that composer. The gateway routes `/api/v1/dashboard` to the dashboard container.
- Scripts that imported `config` or `services` now put `backend-agent` and `backend-core` on `sys.path`.
- `export_openapi.py` and `validate_api_docs.py` still import the root composer, so the combined route list remains what those scripts see.

## Remaining Technical Debt

- The root composer is a second way to boot the agent and the dashboard together.
- The dashboard reads mental-health collections directly.
- Every process loads the full settings schema.
- Login HTTP stays on backend-agent even though the account functions live in backend-core.
- Each process exposes `/health/live` and `/health/ready`. Only the agent payload is on the public port.

## Known Limitations

Docker builds and a live staging health check did not run because the Docker daemon was stopped. Route comparison and pytest did run.

The published repositories do not contain the pre-move commit history. `git subtree split` kept the checkpoint tree and the commits made on those branches afterward. `git filter-repo` was not available. File history before the directory move is still in this repository.

## Verification Commands

```powershell
git status --short
git branch --show-current
git rev-parse HEAD
python -m pytest -q
```

Baseline pytest: `458 passed`.
Final pytest in this repository: `463 passed, 2 warnings in 8.72s`.

Clean clone of backend-core at `4e2798aa`: `import core_app` printed `Zenark Core`. `python -m pytest -q` reported **26 passed**.

Clean clone of backend-agent at `a3e9e654`, after `pip install -r requirements.txt` resolved the pinned backend-core commit: `import app` constructed `Zenark API`. `python -m pytest -q` reported **423 passed, 3 skipped, 0 failed**. The skips are the architecture checks that look for `backend-core` and `dashboard` source trees, which are not inside that repository.

Route comparison: before 154, missing 0, agent dashboard routes 0.

Import smoke, each directory on its own `PYTHONPATH`:

- backend-agent imported `app` and started `Zenark API`.
- backend-core imported `core_app` and started `Zenark core`. `services` was not importable.
- dashboard imported `dashboard_app` and started `Zenark dashboard`. `services` was not importable.
- From backend-agent, `dashboard` was not importable.

```powershell
docker version
```

Result: the Docker pipe `dockerDesktopLinuxEngine` was not available.

| Validation | Result |
|---|---|
| backend-agent import | PASS |
| backend-core import | PASS |
| dashboard import | PASS |
| tests | PASS |
| route inventory | PASS |
| dependency boundaries | PASS |
| Docker backend-agent | BLOCKED |
| Docker backend-core | BLOCKED |
| Docker dashboard | BLOCKED |
| staging startup | BLOCKED |
| Git safety | PASS |
| backend-core GitHub push | PASS |
| backend-agent GitHub push | PASS |
| clean clone backend-core | PASS |
| clean clone backend-agent | PASS |

# MONOLITH REFACTOR STATUS

REFACTOR COMPLETE WITH LIMITATIONS
