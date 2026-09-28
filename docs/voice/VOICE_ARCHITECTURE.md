# Zenark Voice Architecture

Voice is an **adapter** around the existing Zenark text brain. It does not
create a second personality, memory, APM, Graph RAG, pattern engine, or
report system.

```text
Microphone
    ↓
Sarvam Saarika v2.5 STT
    ↓
run_chat_graph  (same pipeline as POST /chat/send)
    ↓
Sarvam Bulbul v3 TTS
    ↓
PCM audio to the client
```

There are two products:

1. **Microphone / standalone STT** — transcribe into the chat box. The user
   edits, then sends with `/chat/send`.
2. **Voice Mode** — WebSocket conversational loop. Transcripts persist as
   normal chat messages.

## Authentication

Both surfaces require a Bearer access token.

- `POST /api/v1/voice/stt` — `Authorization: Bearer <ACCESS_TOKEN>`
- `WS /ws/psychiatrist-voice` — same header, or `?token=<ACCESS_TOKEN>`
  (browsers cannot always set WebSocket headers)

The authenticated user id owns the session. A client `user_id` is never
used to authorize. If a `user_id` is sent on `session.start` and it does
not match the token, the socket is closed.

`SARVAM_API_KEY` stays on the backend. Streamlit and browsers never see it.

## Standalone STT

`POST /api/v1/voice/stt` (multipart field `audio`)

1. Validate size, emptiness, and WAV content (MIME is not trusted).
2. Convert once to 16-bit PCM, 16 kHz, mono WAV.
3. Call Sarvam `POST https://api.sarvam.ai/speech-to-text` with
   `model=saarika:v2.5` (configurable).
4. Return `{ "success": true, "text": "..." }`.

This endpoint does **not** call the LLM and does **not** write messages.

Compatibility path: `POST /voice/stt`.

## Real-time Voice Mode

Canonical: `WS /api/v1/ws/psychiatrist-voice`  
Also: `WS /ws/psychiatrist-voice`

### Session model

A voice session is a live socket, not a second history store.

| Field | Meaning |
|---|---|
| `voice_session_id` | Live adapter id |
| `user_id` | From the token |
| `chat_session_id` | Existing chat session (created if omitted) |
| `request_id` | Correlation id |
| `created_at` / `started_at` / `ended_at` | Lifecycle |

User and assistant **text** are persisted by `run_chat_graph` into
`messages`. Reports, APM, and patterns read that collection. Audio is
not stored.

### Client → server

JSON:

| `type` | Purpose |
|---|---|
| `session.start` | `{ "chat_session_id" }` optional `voice_session_id` |
| `audio.start` | Begin buffering this turn |
| `audio.end` | Stop buffering |
| `turn.end` | Run STT → chat graph → TTS |
| `cancel` | Drop the current buffer; do not call the LLM |
| `session.end` | Close cleanly |

Binary frames after `audio.start` are raw 16-bit PCM (16 kHz mono) **or**
a complete WAV blob (Streamlit sends WAV).

### Server → client

| `type` | Payload |
|---|---|
| `session.started` | `voice_session_id`, `chat_session_id`, `request_id` |
| `transcript.final` | `{ "text" }` recognized speech |
| `assistant.text` | `{ "text", "action_cards" }` from `run_chat_graph` |
| `audio.chunk` | `{ "encoding": "pcm_s16le", "sample_rate": 24000, "channels": 1, "data": "<base64>" }` |
| `assistant.done` | Latency fields (`stt_latency_ms`, `llm_latency_ms`, `tts_latency_ms`, `first_audio_latency_ms`, `total_turn_latency_ms`) |
| `error` | `{ "code", "message", "retryable", "request_id" }` |
| `session.ended` | ids |

`transcript.partial` is **not** emitted. Saarika is called on `turn.end`
with the full utterance. That is the actual latency model — do not treat
this as frame-by-frame streaming STT.

Sarvam TTS currently returns a complete audio payload. The server then
forwards PCM as `audio.chunk` events. First-audio latency includes full
TTS generation for that reply (or the first 500-character TTS segment).

## Language

STT: automatic detection. No stored-language hint is sent, so
code-switching is not pinned to an old preference.

Response language: `users.preferred_language` via the existing resolver.
Spoken language does not write that preference.

HINGLISH stays Roman. TTS uses `en-IN` for HINGLISH and ENGLISH; other
languages use the matching `*-IN` Bulbul code (`ml-IN`, `hi-IN`, …).

## Crisis, context, reports

Voice calls `run_chat_graph`. Crisis keywords, Graph RAG, APM, patterns,
journal/sleep/tasks, and action cards are the text path. After a completed
turn, `run_background_extraction` runs asynchronously and must not delay
audio.

A reconnect with the same `chat_session_id` continues the same message
history (session resume still applies). The same user text at the session
tip is not regenerated (`find_completed_user_turn`). The voice session
also skips a second LLM call when the same transcript is submitted twice
on the same live socket.

## Audio formats

| Direction | Format |
|---|---|
| Client → STT | WAV or PCM16; converted once to 16 kHz mono WAV |
| Sarvam STT | 16-bit PCM, 16 kHz, mono WAV |
| Sarvam TTS | 16-bit PCM, 24 kHz, mono (often wrapped in WAV/base64) |
| Client ← TTS | `audio.chunk` PCM16 little-endian, 24 kHz, mono |

Limits (Settings / `.env`):

| Variable | Default |
|---|---|
| `MAX_AUDIO_FILE_SIZE` | 10485760 (10 MiB) |
| `MAX_AUDIO_DURATION_SECONDS` | 60 |
| `MAX_CONCURRENT_VOICE_SESSIONS` | 50 |
| `VOICE_STT_TIMEOUT` | 30 |
| `VOICE_TTS_TIMEOUT` | 30 |
| `VOICE_LLM_TIMEOUT` | 45 |

One live voice socket per user per process. Process-local rate limits:
20 STT / minute, 10 session starts / minute (see `RATE_LIMIT_VOICE_*`).
Multiple API replicas do not share these counters unless a shared
`RATE_LIMIT_BACKEND` is configured.

## Errors

| Situation | Behavior |
|---|---|
| Bad / empty / huge audio | 400 or WS `INVALID_REQUEST` |
| Missing token | 401 / WS close 4401 |
| Foreign voice session | WS `FORBIDDEN` / close 4403 |
| Sarvam STT down | 502 / WS `STT_FAILED` (`retryable`) |
| LLM timeout | WS `LLM_TIMEOUT` (`retryable`) |
| TTS down | Assistant text is still sent; WS `TTS_FAILED` |
| Disconnect | Session removed; completed turns already in `messages` stay |

## Observability

Logs include `request_id`, `voice_session_id`, `chat_session_id`, a
**hash** of `user_id`, provider/model names, and stage latencies.
Raw audio and full transcripts are not written at INFO.

## Local development

1. Set `SARVAM_API_KEY` in `.env` (never commit it).
2. `python run.py`
3. Streamlit: microphone fills the review box; **Send transcription**
   calls `/chat/send`. **Start Voice** uses the WebSocket.

Without a key, STT/TTS return a controlled provider error. Text chat
keeps working.

## Production notes

- Do not ship the API key to any client.
- Do not use the API process as a meditation CDN; voice PCM is
  connection-scoped.
- Horizontal scale needs a shared rate-limit store and a cap on
  concurrent Sarvam calls (`VOICE_PROVIDER_CONCURRENCY`).
- Streamlit Voice Mode opens a new socket per recorded turn. Native
  mobile clients should keep one socket for the session.
