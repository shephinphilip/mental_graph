# Therapeutic AI Companion

> An empathetic, context-aware AI companion for mental health support — built with **FastAPI**, **LangGraph**, **MongoDB Graph RAG**, **AWS Bedrock**, and **Streamlit**.

---

## Table of Contents

1. [Overview](#overview)
2. [Key Features](#key-features)
3. [Architecture](#architecture)
4. [Project File Reference](#project-file-reference)
5. [Data Flow Walkthrough](#data-flow-walkthrough)
6. [Database Schema](#database-schema)
7. [MongoDB Knowledge Graph](#mongodb-knowledge-graph)
8. [API Endpoints](#api-endpoints)
9. [SSE Event Reference](#sse-event-reference)
10. [Configuration (.env)](#configuration-env)
11. [Quick Start](#quick-start)
12. [Running Tests](#running-tests)
13. [Security Model](#security-model)
14. [Crisis Safeguard Protocol (Section 8)](#crisis-safeguard-protocol-section-8)
15. [Extending the Project](#extending-the-project)

---

## Overview

The Therapeutic AI Companion is a full-stack mental health support application that combines:

- **Conversational AI** (AWS Bedrock with Sarvam AI fallback) for warm, empathetic dialogue
- **MongoDB Graph RAG** (Retrieval-Augmented Generation using `$graphLookup` and deterministic node IDs) for long-term relational memory
- **MongoDB** for message history, mood logs, habits, and extracted session insights
- **Inline Action Cards** — structured UI components rendered in-chat (habit, booking, tool, content, task)
- **E2EE Encryption** — all stored message content is Fernet-encrypted at rest
- **Crisis Safeguard Protocol** — immediate helpline surfacing on detection of crisis signals

The backend is a **FastAPI** ASGI application; the frontend is a **Streamlit** web app. Both are independent services that communicate over HTTP.

---

## Key Features

| Feature | Detail |
|---|---|
| 🧠 MongoDB Graph RAG | High-performance `$graphLookup` traversal with deterministic node IDs enriches LLM prompts |
| ⚡ Sub-500ms Session Resumption | Indexed MongoDB query restores the last 10 messages instantly on app open |
| 📡 SSE Token Streaming | Real-time token delivery via Server-Sent Events with pre-first-token fallback contract |
| 🃏 Inline Action Cards | LLM-emitted structured cards (TOOL, HABIT, TASK, BOOKING, CONTENT) rendered in the UI |
| 🔒 E2EE Encryption | Fernet AES encryption on all message content written to MongoDB |
| 🕵️ PII Anonymization | Phone, email, and Aadhaar redaction before text reaches external LLM APIs |
| 🆘 Crisis Protocol | Keyword pre-check + LLM signal detection + immediate helpline resources |
| 🔄 LLM Failover | AWS Bedrock (Gemma 2) primary → Sarvam AI fallback with explicit startup validation |
| 🌐 Multi-language | Supports Hinglish, Tamil, Telugu, Kannada, Marathi, Bengali, Gujarati, Punjabi |
| 📊 Insight Extraction | Background LLM pipeline extracts emotions, themes, and crisis flags per turn |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                       CLIENT LAYER                                  │
│                                                                     │
│   ┌───────────────────────┐    ┌─────────────────────────────────┐  │
│   │   Streamlit UI        │    │    Mobile / Web App (future)   │  │
│   │   streamlit_app.py    │    │    Any HTTP client             │  │
│   └──────────┬────────────┘    └──────────────┬──────────────────┘  │
└──────────────┼───────────────────────────────┼────────────────────┘
               │ HTTP (REST / SSE)              │ HTTP (REST / SSE)
               ▼                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     FASTAPI BACKEND (app.py)                        │
│                                                                     │
│   POST /chat/send         ──► services/graph.py (LangGraph)         │
│   POST /chat/stream       ──► services/streaming.py (SSE)           │
│   GET  /chat/session/resume ► services/session_resume.py            │
│   GET  /health                                                       │
└────────────────────┬────────────────────────────────────────────────┘
                     │ Background Tasks
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│               BACKGROUND EXTRACTION (services/extraction.py)        │
│                                                                     │
│   Task 1: Insight extraction   ──► MongoDB (user_insights)           │
│   Task 2: Graph tuple extraction ► Neo4j (knowledge graph)           │
└───────────────┬─────────────────────────────────────────────────────┘
                │
     ┌──────────┴──────────┐
     ▼                     ▼
┌─────────┐          ┌──────────────────────────────┐
│ MongoDB │          │         Neo4j                │
│         │          │   (Emotional Knowledge Graph) │
│ messages│          │                              │
│ users   │          │  User ──EXPERIENCES──► Anxiety│
│ mood_logs         │  Anxiety ─TRIGGERED_BY─► Work │
│ habits  │          │  User ──TRIED_TOOL──► BodyScan│
│ insights│          └──────────────────────────────┘
└─────────┘

                    LLM PROVIDERS
               ┌──────────────────────┐
               │  Primary: Gemini Pro  │
               │  Fallback: GPT-4o     │
               └──────────────────────┘
```

### LangGraph Pipeline (per chat turn)

```
START
  │
  ▼
fetch_context          ← MongoDB: history, memory, moods, habits
  │
  ▼
retrieve_graph_context ← Neo4j: k-hop emotional subgraph → natural language facts
  │
  ▼
generate               ← LLM: Gemini (→ GPT-4o fallback) with full context prompt
  │
  ▼
format_output          ← Parse action cards, persist messages, build response
  │
  ▼
END → { session_id, reply, action_cards }
```

---

## Project File Reference

```
mental_health/
│
├── app.py                  FastAPI application entry point & route definitions
├── config.py               Centralised settings (pydantic-settings, .env loader)
├── database.py             MongoDB (Motor) & Neo4j connection lifecycle management
├── llm_provider.py         Dual-LLM provider: Gemini primary + GPT-4o fallback
├── prompts.py              All LLM system prompt templates (therapeutic, extraction, graph)
├── schemas.py              Pydantic models for requests, responses, and pipelines
├── streamlit_app.py        Streamlit web UI (chat interface, action cards, crisis banner)
├── run.py                  Development server launcher (uvicorn)
├── scheduler.py            (Reserved) Cron/scheduled task runner
│
├── services/
│   ├── __init__.py
│   ├── action_cards.py     Regex-based action card parser (<<<ACTION_CARD ... ACTION_CARD>>>)
│   ├── context.py          MongoDB context aggregator (memory, moods, habits)
│   ├── extraction.py       Async background metadata extraction pipeline
│   ├── graph.py            LangGraph conversational state machine (4-node pipeline)
│   ├── graph_rag.py        Neo4j Graph RAG service (read & write paths)
│   ├── security.py         Fernet E2EE encryption + PII anonymization
│   ├── session_resume.py   Sub-500ms session state restoration
│   └── streaming.py        SSE token streaming engine (8-step pipeline)
│
├── tests/
│   ├── __init__.py
│   ├── test_action_cards.py
│   ├── test_fallback.py
│   ├── test_graph.py
│   ├── test_graph_rag.py
│   ├── test_security.py
│   ├── test_session_resume.py
│   └── test_streaming.py
│
├── .env                    Local secrets (never commit to git)
├── .env.example            Template for required environment variables
├── requirements.txt        Python package dependencies
└── crontab.txt             System crontab entries for scheduled jobs
```

### Detailed File Descriptions

#### Root-Level Files

| File | Purpose |
|---|---|
| [`app.py`](app.py) | Defines all FastAPI routes. Each route validates the request, calls the appropriate service, and returns a structured response. Background extraction is scheduled here. |
| [`config.py`](config.py) | Single source of truth for all configuration. Uses `pydantic-settings` to load and validate env vars. Cached via `@lru_cache` — safe to call from any module. |
| [`database.py`](database.py) | Manages MongoDB and Neo4j client lifecycles. Clients are stored on `app.state` and injected into routes via `Depends()`. Uses the FastAPI `lifespan` context manager. |
| [`llm_provider.py`](llm_provider.py) | Returns a `RunnableWithFallbacks`: Gemini primary, GPT-4o fallback. LangChain provider imports are lazy to avoid heavy startup costs. |
| [`prompts.py`](prompts.py) | Contains three prompt templates: `SYSTEM_PROMPT` (therapeutic companion), `EXTRACTION_PROMPT` (insight extraction), `GRAPH_EXTRACTION_PROMPT` (knowledge graph population). |
| [`schemas.py`](schemas.py) | All Pydantic models. Divided into: Action Cards, Chat API, Extraction Pipeline, Graph RAG, and Session Resumption sections. |
| [`streamlit_app.py`](streamlit_app.py) | Full Streamlit web application. Handles session auto-resume, chat rendering, action card display, error handling, and crisis helplines sidebar. |
| [`run.py`](run.py) | One-liner development server launcher: `python run.py`. Starts uvicorn with hot-reload on port 8000. |

#### `services/` Package

| File | Responsibility |
|---|---|
| [`action_cards.py`](services/action_cards.py) | Parses `<<<ACTION_CARD {...} ACTION_CARD>>>` blocks from LLM output using a compiled regex. Validates JSON against the `ActionCard` schema. Malformed blocks are skipped, not raised. |
| [`context.py`](services/context.py) | Aggregates user context from MongoDB: `memory_summary`, `key_takeaways` (users), mood entries (mood_logs), and active habits (habit_events). Returns formatted strings for prompt injection. |
| [`extraction.py`](services/extraction.py) | Background pipeline run after each chat response. Two isolated tasks: (1) LLM-based insight extraction → MongoDB `user_insights`; (2) LLM-based graph tuple extraction → Neo4j. Each task has its own try/except boundary. |
| [`graph.py`](services/graph.py) | The LangGraph state machine. Defines `ChatState`, four pipeline node functions, and the public `run_chat_graph()` entry point. The compiled graph is lazily cached. |
| [`graph_rag.py`](services/graph_rag.py) | Two-path Neo4j service. **Read:** Variable-length Cypher traversal from User node, formatted as bullet facts. **Write:** MERGE-based upsert of `GraphTuple` objects. Also creates startup constraints. |
| [`security.py`](services/security.py) | `encrypt_payload()` / `decrypt_payload()` for Fernet E2EE (PBKDF2-derived key, `enc::` prefix). `anonymize_text()` for PII redaction (phone, email, Aadhaar). Configurable via settings. |
| [`session_resume.py`](services/session_resume.py) | Resolves session ID, loads last 10 messages (decrypted), computes dropped-session context string, and fetches the user's active emotional state. Target: < 500ms. |
| [`streaming.py`](services/streaming.py) | 8-step SSE pipeline. Pre-checks for crisis keywords, fetches context, streams tokens from LLM, post-processes for action cards, persists messages with E2EE encryption. |

---

## Data Flow Walkthrough

### Standard Chat Turn (`POST /chat/send`)

```
User sends: "I've been really anxious about my job interview tomorrow."

1. FastAPI validates ChatMessageRequest (user_id, session_id, message)
2. run_chat_graph() invoked:

   a. fetch_context_node:
      - MongoDB users → memory_summary + key_takeaways
      - MongoDB mood_logs → last 7 days of entries
      - MongoDB habit_events → active habits
      - MongoDB messages → last 20 messages (session history)

   b. retrieve_graph_context_node:
      - Neo4j MATCH (u:User {id: $user_id})-[r*1..2]-(connected)
      - Returns: "• Trigger: Job Interview — via: experiences → triggered by"

   c. generate_node:
      - Formats SYSTEM_PROMPT with graph + memory + moods + habits
      - Builds [SystemMessage, ...history, HumanMessage("...job interview...")]
      - Calls Gemini (→ GPT-4o if error)
      - Returns raw LLM text (may include <<<ACTION_CARD ... ACTION_CARD>>>)

   d. format_output_node:
      - parse_action_cards() → clean_reply + [ActionCard(TOOL_CARD, "Breathing Exercise")]
      - MongoDB messages.insert_one(user message)
      - MongoDB messages.insert_one(assistant reply)
      - MongoDB action_card_logs.insert_many([...])

3. Response returned: {session_id, reply, action_cards: [...]}

4. BackgroundTask: run_background_extraction()
   - Task 1: LLM → {detected_emotions: ["anxious"], crisis_signal: false}
             → MongoDB user_insights.insert_one(...)
   - Task 2: LLM → {tuples: [User -EXPERIENCES-> Anxiety, Anxiety -TRIGGERED_BY-> Job Interview]}
             → Neo4j MERGE nodes + relationships
```

### Streaming Chat Turn (`POST /chat/stream`)

```
Same as above but:
- Step 3 uses llm.astream() instead of llm.ainvoke()
- Each token yielded as: "event: token\ndata: {"token": "..."}\n\n"
- After full text assembled: action cards emitted as separate events
- "event: done\ndata: {"status": "completed"}\n\n" always last
```

---

## Database Schema

### MongoDB Collections

#### `messages`
```json
{
  "_id": ObjectId,
  "session_id": "session_001",
  "user_id": "user_001",
  "role": "user" | "assistant",
  "content": "enc::gAAAAA...",   // Fernet-encrypted
  "created_at": ISODate
}
```
**Recommended indexes:**
```
{ session_id: 1, user_id: 1, created_at: -1 }   // Session history (compound)
{ user_id: 1, created_at: -1 }                   // Session auto-detection
```

#### `users`
```json
{
  "_id": ObjectId,
  "user_id": "user_001",
  "memory_summary": "User has been dealing with work anxiety...",
  "key_takeaways": ["Breathing exercises help", "Avoids social events when stressed"],
  "active_emotional_state": "anxious",
  "crisis_flag": false,
  "crisis_flagged_at": null,
  "crisis_session_id": null
}
```

#### `mood_logs`
```json
{
  "_id": ObjectId,
  "user_id": "user_001",
  "mood": "anxious",
  "score": 3,
  "note": "Couldn't focus all day",
  "logged_at": ISODate
}
```

#### `habit_events`
```json
{
  "_id": ObjectId,
  "user_id": "user_001",
  "title": "Morning Journal",
  "frequency": "daily",
  "streak": 12,
  "status": "active" | "paused" | "completed"
}
```

#### `user_insights`
```json
{
  "_id": ObjectId,
  "user_id": "user_001",
  "session_id": "session_001",
  "created_at": ISODate,
  "detected_emotions": ["anxious", "hopeful"],
  "core_themes": ["work_burnout", "interview_anxiety"],
  "suggested_habits": ["daily journaling"],
  "crisis_signal_detected": false,
  "escalation_recommended": false,
  "insight_summary": "User is anxious about an upcoming job interview."
}
```

#### `action_card_logs`
```json
{
  "_id": ObjectId,
  "session_id": "session_001",
  "user_id": "user_001",
  "card": {
    "card_type": "TOOL_CARD",
    "title": "4-7-8 Breathing Exercise",
    "subtitle": "Calm anxiety in 5 minutes",
    "action_payload": { "resource_id": "breathing_478" }
  },
  "created_at": ISODate
}
```

---

## Neo4j Knowledge Graph

### Node Labels

| Label | Primary Key | Description |
|---|---|---|
| `User` | `id` | The platform user — singleton per `user_id` |
| `Entity` | `name` | A person, place, or thing (e.g. "Mother", "Work") |
| `Event` | `title` | A specific life event (e.g. "Job Interview") |
| `Emotion` | `state` | An emotional state (e.g. "Anxiety") |
| `Trigger` | `description` | A situational trigger (e.g. "Work Deadlines") |
| `CopingTool` | `name` | A technique that helps (e.g. "8-Min Body Scan") |
| `Session` | `session_id` | A conversation session (temporal linking) |

### Relationship Types

| Relationship | Example |
|---|---|
| `EXPERIENCES` | `(User)-[EXPERIENCES]->(Anxiety)` |
| `TRIGGERED_BY` | `(Anxiety)-[TRIGGERED_BY]->(Work Deadlines)` |
| `ASSOCIATED_WITH` | `(User)-[ASSOCIATED_WITH]->(Mother)` |
| `TRIED_TOOL` | `(User)-[TRIED_TOOL]->(Body Scan)` |
| `HELPED_WITH` | `(Body Scan)-[HELPED_WITH]->(Insomnia)` |
| `FOLLOWED_BY` | `(Session_001)-[FOLLOWED_BY]->(Session_002)` |
| `PARTICIPATED_IN` | `(User)-[PARTICIPATED_IN]->(Job Interview)` |

### Uniqueness Constraints (created at startup)
```cypher
CREATE CONSTRAINT IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (t:Trigger) REQUIRE t.description IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (c:CopingTool) REQUIRE c.name IS UNIQUE;
```

---

## API Endpoints

### `POST /chat/send`
**Request:**
```json
{
  "user_id": "user_001",
  "session_id": "session_001",
  "message": "I'm feeling really overwhelmed today"
}
```
**Response (200):**
```json
{
  "session_id": "session_001",
  "reply": "It sounds like today has been especially heavy...",
  "action_cards": [
    {
      "card_type": "TOOL_CARD",
      "title": "4-7-8 Breathing",
      "subtitle": "A 5-minute breathing exercise",
      "action_payload": { "resource_id": "breathing_478" }
    }
  ]
}
```

---

### `POST /chat/stream`
Same request body as `/chat/send`. Returns `text/event-stream`.

---

### `GET /chat/session/{user_id}/resume`
**Query params:** `session_id` (optional)

**Response (200):**
```json
{
  "user_id": "user_001",
  "session_id": "session_001",
  "is_resumed": true,
  "last_message_timestamp": "2026-08-04T12:30:00Z",
  "dropped_session_context": "Resuming after last AI response: 'Let's try...'",
  "recent_messages": [
    { "role": "user", "content": "I'm anxious", "timestamp": "2026-08-04T12:29:00Z" },
    { "role": "assistant", "content": "Let's try...", "timestamp": "2026-08-04T12:29:30Z" }
  ],
  "active_emotional_state": "anxious"
}
```

---

### `GET /health`
```json
{ "status": "healthy", "service": "therapeutic-ai-chatbot", "version": "0.3.0" }
```

---

## SSE Event Reference

Events emitted by `POST /chat/stream`:

| Event | Trigger | Payload |
|---|---|---|
| `crisis_alert` | Crisis keyword detected | `{title, message, helplines[], action_card}` |
| `token` | Each LLM output chunk | `{"token": "...partial text..."}` |
| `action_card` | Card parsed post-generation | `{card_type, title, subtitle, action_payload}` |
| `error` | LLM stream exception | `{"error": "...error message..."}` |
| `done` | Stream complete | `{"status": "completed"}` |

**Example SSE response:**
```
event: token
data: {"token": "It sounds like "}

event: token
data: {"token": "today has been heavy."}

event: action_card
data: {"card_type": "TOOL_CARD", "title": "Breathing Exercise", ...}

event: done
data: {"status": "completed"}
```

---

## Configuration (.env)

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | ✅ | — | Google Gemini API key |
| `OPENAI_API_KEY` | ✅ | — | OpenAI API key (fallback LLM) |
| `MONGODB_URI` | ✅ | `mongodb://localhost:27017` | MongoDB connection string |
| `DATABASE_NAME` | | `mental_health` | MongoDB database name |
| `NEO4J_URI` | ✅ | `neo4j://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | ✅ | — | Neo4j password |
| `GRAPH_TRAVERSAL_DEPTH` | | `2` | k-hop depth for graph context |
| `PRIMARY_MODEL` | | `gemini-1.5-pro` | Primary LLM model name |
| `FALLBACK_MODEL` | | `gpt-4o` | Fallback LLM model name |
| `LLM_TEMPERATURE` | | `0.7` | LLM sampling temperature |
| `ENCRYPTION_SECRET_KEY` | ✅ | — | Secret for Fernet key derivation (32+ bytes) |
| `ENFORCE_PII_ANONYMIZATION` | | `true` | Enable/disable PII redaction |
| `MAX_HISTORY_MESSAGES` | | `20` | Message history window for LLM |
| `MOOD_LOG_LOOKBACK_DAYS` | | `7` | Days of mood history in prompt |

---

## Adaptive Psychological Memory

APM is a separate, consent-gated temporal graph. It supplements
`graph_nodes`/`graph_relationships`; it does not replace them.

### Collections

- `apm_nodes`: stable, user-scoped `TRIGGER`, `LATENT_STATE`,
  `INTERVENTION`, `OUTCOME`, and `CONTEXT` concepts. Raw user IDs are not
  embedded in object IDs.
- `apm_edges`: temporal transitions and recovery evidence. Raw success and
  failure counts are retained alongside a Bayesian score and relation-specific
  decay rate.
- `apm_events`: minimized, append-only observations and idempotent intervention
  feedback. The unique execution nonce prevents duplicate reinforcement.

### Recommendation boundary

APM is optimized for explicitly reported benefit, never time spent, message
count, opens, or return frequency. A memory may produce one action card only
when:

1. personalization consent is enabled;
2. node and edge confidence are at least `0.4`;
3. the edge has an explicit successful outcome;
4. the latest explicit outcome is not a failure;
5. lexical evidence makes it relevant to the current turn; and
6. the turn is not a crisis turn.

Bootstrapped and temporal-fallback memories are background-only. Outbound JITAI
nudges are intentionally disabled; temporal eligibility is stored for a future
release with separate opt-in, quiet hours, cooldowns, and frequency caps.

### Feedback and scoring

Action-card feedback is authenticated and user-scoped. Event insertion is
idempotent on `(user_id, execution_nonce, event_type)`. Edge counters, version,
timestamp, and Bayesian score are updated in one atomic MongoDB pipeline update.
`TRIGGERS` and `EVOLVES_INTO` decay slowly; `RECOVERED_BY` decays faster and
receives an additional bounded penalty after explicit failure.

Disabling personalization stops APM extraction and retrieval. Existing APM
records are not used while disabled. `DELETE /api/memory` permanently removes
the authenticated user's `apm_nodes`, `apm_edges`, and `apm_events`; retention
or export jobs can operate on the same user-scoped keys.

---

## Quick Start

### Prerequisites
- Python 3.11+
- MongoDB (local or Atlas)
- AWS Bedrock access to the configured Gemma primary and Sarvam fallback

### 1. Clone & install dependencies

```bash
git clone <repo-url>
cd mental_health

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys and database credentials
```

### 3. Start the FastAPI backend

```bash
# Option A: using run.py
python run.py

# Option B: direct uvicorn
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at: `http://localhost:8000`
Interactive docs: `http://localhost:8000/docs`

### 4. Start the Streamlit UI

Open a **second terminal** (with the venv activated):

```bash
streamlit run streamlit_app.py
```

The UI will open at: `http://localhost:8501`

---

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run a specific test file
pytest tests/test_graph.py -v

# Run with asyncio mode (required for async tests)
pytest tests/ -v --asyncio-mode=auto
```

> **Note:** Tests use mocked database clients and LLMs — they do not require a live MongoDB or Neo4j instance.

---

## Security Model

### Encryption at Rest
- All message `content` fields written to MongoDB are encrypted using **Fernet** (AES-128-CBC + HMAC).
- The Fernet key is derived from `ENCRYPTION_SECRET_KEY` via **PBKDF2-HMAC-SHA256** (100,000 iterations).
- Encrypted strings are prefixed with `enc::` for easy detection.
- `decrypt_payload()` returns the original string on key mismatch or corruption rather than raising, to prevent data loss.

### PII Anonymization
Before user text is sent to AWS Bedrock, the following patterns are redacted:
- **Phone numbers** (Indian mobile `+91xxxxxxxxxx` + US format) → `[PHONE_REDACTED]`
- **Email addresses** → `[EMAIL_REDACTED]`
- **Aadhaar numbers** (12-digit) → `[ID_REDACTED]`

Control with: `ENFORCE_PII_ANONYMIZATION=false` to disable (development only).

### Secrets Management
- Never commit `.env` to version control — it is listed in `.gitignore`.
- Use `.env.example` (with placeholder values) as the only committed reference.
- In production, inject secrets via environment variables (Docker secrets, AWS SSM, Kubernetes secrets).

---

## Crisis Safeguard Protocol (Section 8)

The system implements a two-tier crisis detection protocol:

### Tier 1 — Immediate Keyword Pre-Check (< 100ms)
Before any LLM call in the streaming path, the user's message is scanned for hardcoded crisis keywords:
```python
["suicide", "end my life", "want to die", "kill myself",
 "self harm", "cut myself", "overdose", "no reason to live"]
```
On detection:
- A `crisis_alert` SSE event is immediately emitted with helpline resources.
- The LLM is **not called** — the stream ends with a `done` event.
- The exchange is persisted to MongoDB.

### Tier 2 — LLM Extraction Signal (background)
The `run_background_extraction` pipeline asks the LLM to set `"crisis_signal_detected": true` in the extraction JSON. On detection:
- A `CRITICAL`-level log entry is created (visible in alerting dashboards).
- The user's MongoDB document is flagged with `crisis_flag: true`, `crisis_flagged_at`, and `crisis_session_id`.

### Crisis Helplines Surfaced
| Helpline | Number | Hours |
|---|---|---|
| Tele-MANAS | `14416` | 24/7 |
| Vandrevala Foundation | `+91 9999 666 555` | 24/7 |
| KIRAN Helpline | `1800-599-0019` | 24/7 |
| AASRA | `+91 9820466726` | 24/7 |

---

## Extending the Project

### Adding a New Action Card Type
1. Add the new type to `CardType` enum in [`schemas.py`](schemas.py).
2. Document the new type in the `SYSTEM_PROMPT` in [`prompts.py`](prompts.py).
3. Add rendering logic in [`streamlit_app.py`](streamlit_app.py) `render_action_card()`.

### Adding a New Crisis Keyword
Edit `_CRISIS_KEYWORDS` list in [`services/streaming.py`](services/streaming.py).

### Changing the LLM Model
Update `PRIMARY_MODEL` or `FALLBACK_MODEL` in `.env` (or override in `config.py` defaults). No code changes required.

### Adding a New Graph Relationship Type
1. Add to `GraphRelationType` enum in [`schemas.py`](schemas.py).
2. Add it to the Cypher `WHERE type(rel) IN [...]` list in [`services/graph_rag.py`](services/graph_rag.py).
3. Update the `GRAPH_EXTRACTION_PROMPT` in [`prompts.py`](prompts.py) to instruct the LLM to use the new type.

### Adding a Scheduled Background Job
Use [`scheduler.py`](scheduler.py) (currently empty) with APScheduler or a system crontab (`crontab.txt`) for periodic tasks like:
- Generating weekly mood summaries
- Pruning old session data
- Refreshing graph constraints
