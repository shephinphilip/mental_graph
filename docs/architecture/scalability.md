# Scalability model

This is a planning model, not a measured capacity claim.

The target in the brief is **approximately 1,000,000 total users or
lifetime requests at scale**. That is not 1,000,000 requests/second
and it is not a certified throughput number. No load test in this
repository has produced a measured ceiling.

## Assumptions (explicit)

| Input | Assumed value | Why |
| --- | --- | --- |
| Registered users | 1,000,000 | product target, not current |
| Daily active | 5% = 50,000 | early-stage mental-health apps are bursty |
| Requests / active user / day | 20 | login + 8 chat + 4 track + 4 journal/tasks + 4 other |
| Peak factor | 8× average | school-hours clustering |
| Average LLM duration | 2.5 s | Bedrock Gemma/Sarvam, untested here |
| Concurrent streaming share | 30% of peak chat | SSE holds a worker |
| Mongo ops / chat request | 8–15 | context + persist + later extraction |

Derived (from those assumptions only):

- Daily requests ≈ 1,000,000
- Average RPS ≈ 12
- Peak RPS ≈ 100
- Peak concurrent streams ≈ 30–80
- Needed LLM concurrency ≈ peak streams + JSON chat, **Bedrock quota bound**

## What the code can do today

- One Motor client per process, pool sized by `MONGO_MAX_POOL_SIZE` (default 100).
- In-process rate limits (not shared across replicas).
- LLM semaphore + timeout/retry only when `APP_ENV` is `production` or `staging`.
- Background extraction is FastAPI `BackgroundTasks` — lost on process death, duplicated if two replicas retry the same turn without idempotency keys (graph/APM upserts are mostly idempotent; extraction LLM calls are not).

## Deployment model

| Process | Role | Horizontal note |
| --- | --- | --- |
| `uvicorn app:app` | HTTP API | safe to replicate behind a load balancer |
| `scheduler.py` | stub cron thread | **not safe** with multiple API instances; do not start it in the API process |
| Streamlit | dev UI | call the API only; never Mongo |

Production startup:

```
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 2
```

No `--reload`.

## Likely bottlenecks

| Bottleneck | Current state | Risk | Measurement | Possible solution |
| --- | --- | --- | --- | --- |
| AWS Bedrock latency/quota | one client per call via LangChain | highest | LLM latency + 429s | provisioned throughput, queue generation |
| SSE connections | one asyncio task per stream | high | disconnect count | more workers, shorter sessions |
| Mongo | shared pool, domain indexes | medium | pool + op latency | Atlas tier, watch slow queries |
| Report generation | full transcript slice + LLM | high | `/session/report` latency | keep context compact (already truncated) |
| Background extraction | in-process | high at scale | worker failures | durable queue (not built) |
| Local meditation audio | files on disk | high if served from API | bandwidth | object storage + CDN |
| Prompt size | unified context | medium | token counts | keep readers bounded |
| Rate limiter | memory | incorrect under multi-instance | 429 skew | Redis backend (not wired) |
