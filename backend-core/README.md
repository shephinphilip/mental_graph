# Zenark backend-core

Shared platform package used by backend-agent and the school dashboard. It is a Python library. The optional `core_app` process only exposes process health probes. Product traffic does not call this process over HTTP.

## Owns

- Settings and logging (`config`)
- Mongo client and index registration (`database`, `db`)
- Encryption and anonymization (`backend_core.security`)
- Accounts and access tokens (`backend_core.users`)
- Bearer dependency (`backend_core.deps`)
- School tenant key (`backend_core.tenancy`)
- Academic mark reads (`backend_core.marks`)
- Request IDs, middleware, error envelopes, rate limit
- Bedrock client (`llm_provider`)

## Does not own

Mental-health chat, safety, APM, EOS, GDS, Graph RAG, Exam Buddy, or dashboard routes.

## Install

```powershell
pip install -e .
```

From another repository, pin a commit:

```text
zenark-backend-core @ git+https://github.com/Zenark-2025/backend-core.git@<commit>
```

## Environment

Copy placeholders into a gitignored `.env` at the repository root next to this package when running it alone. Required names include `MONGODB_URI`, `DATABASE_NAME`, `ENCRYPTION_SECRET_KEY`, and `AUTH_SIGNING_SECRET`. Staging must not use the placeholder secrets or the database name `mental_health`. AWS keys are required only when a process actually calls Bedrock.

## Local startup

```powershell
python run.py
```

Listens on port 8001. `GET /health/live` and `GET /health/ready` are process probes.

## Docker

```powershell
docker build -f Dockerfile -t zenark-core .
```

The monolith build context is the parent repository. A standalone clone builds from this directory. See that clone's Dockerfile if it differs.

## Tests

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

Inside the monolith, run from the repository root so agent and dashboard tests are included:

```powershell
python -m pytest -q
```
