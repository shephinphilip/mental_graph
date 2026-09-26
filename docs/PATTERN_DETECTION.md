# Pattern Detection Engine

Longitudinal user patterns for Zenark. Separate from Graph RAG and APM.

## Principle

Move from “AI remembers what happened” to “AI understands what repeatedly
happens for this user, with evidence, confidence, and uncertainty — without
treating correlation as causation.”

## Pipeline

```
RAW USER DATA
  → Data Normalization (adapters)
  → Observation Extraction
  → Pattern Detection Engine
  → Validation / scoring / decay
  → Pattern Store (user_patterns + pattern_evidence)
  → Relevance Retrieval (≤3)
  → LLM {pattern_context}
  → Conversational response
```

Fast path for chat: auth → context (incl. bounded pattern retrieval) → reply.  
Background path: post-turn extraction Task 4 → detect/upsert patterns.

## Dynamic 1–10 risk window

Each user turn is scored 1.0–10.0 by `services/risk_assessor.py` (zero-LLM).
Scores land on `SessionExtraction` (`risk_intensity_score`, `valence`,
`arousal`, `confidence_score`) and in `user_risk_turns`.

| Setting | Default | Role |
|---|---|---|
| `RISK_HIGH_THRESHOLD` | 8.0 | High-distress cutoff |
| `RISK_WINDOW_TURNS` | 3 | Consecutive high turns before persistence |
| `RISK_ROLLING_HOURS` | 6 | Alternate rolling-average window |
| `RISK_CARD_COOLDOWN_HOURS` | 72 | After dismiss / not-helpful |

A single spike never queues a card. Acute crisis keywords still use the
existing emergency path and are **excluded** from consecutive-high
reinforcement. Persistent high scores elevate
`ESTABLISHED_PERSISTENT_DISTRESS` and attach a `PSYCHIATRIST_REFERRAL`
booking card plus `{action_card_context}` so the LLM can acknowledge it
without emitting a second card. `POST /api/patterns/feedback` with
`DISMISS` respects the cooldown.

**Locked-in guarantees**

- **Autonomy:** `DISMISS` / “Not now” starts a 72-hour card cooldown
  (`RISK_CARD_COOLDOWN_HOURS`). The pattern can remain; the popup does not.
- **LLM awareness:** `{action_card_context}` tells the model the UI will
  show the card, so it acknowledges once and does not duplicate the CTA.
- **Acute keyword integrity:** `/chat/send` and `/chat/stream` both
  fast-track crisis language immediately (helplines + crisis card). That
  path does not wait for three high-scoring turns and does not reinforce
  the persistent-distress psychiatrist card.

## Collections

### `user_patterns`

| Field | Meaning |
|---|---|
| `user_id` | Owner (isolation key) |
| `pattern_id` | Stable id (`pat_…`) |
| `fingerprint` | Unique per user pattern signature |
| `pattern_type` | TEMPORAL / BEHAVIORAL / CROSS_DOMAIN / ACADEMIC / INTERVENTION_RESPONSE / RECURRENCE / CHANGE_POINT |
| `domains` | e.g. mood, academic, meditation |
| `description` | Natural-language, non-diagnostic |
| `observations` | Compact feature/condition pairs (not raw events) |
| `evidence_count` | Supporting count |
| `confidence` / `strength` | [0,1] |
| `status` | OBSERVATION → EMERGING → ESTABLISHED → INACTIVE |
| `confirm_count` / `disagree_count` / `contradiction_count` | Feedback |
| `first_observed_at` / `last_observed_at` | Lifecycle |
| `decay_rate` | Per-day decay |

### `pattern_evidence` (append-only)

`user_id`, `pattern_id`, `source`, `feature`, `value`, `event_key` (unique),
`confidence`, `provenance`, `event_at`, `created_at`.

No large raw journal/message bodies.

## Configurable thresholds (`config.Settings` / env)

| Setting | Default | Role |
|---|---|---|
| `PATTERN_EMERGING_MIN_EVIDENCE` | 3 | OBSERVATION → EMERGING |
| `PATTERN_ESTABLISHED_MIN_EVIDENCE` | 5 | → ESTABLISHED |
| `PATTERN_RETRIEVAL_MAX` | 3 | Max patterns in prompt |
| `PATTERN_RETRIEVAL_MIN_CONFIDENCE` | 0.4 | Floor for injection |
| `PATTERN_DECAY_PER_DAY` | 0.01 | Time decay |
| `PATTERN_INACTIVE_DAYS` | 45 | Mark inactive |
| `PATTERN_BASELINE_WINDOW_DAYS` | 30 | Personal baseline |
| `PATTERN_LOOKBACK_DAYS` | 60 | Adapter window |
| `PATTERN_FEEDBACK_CONFIRM_BOOST` | 0.08 | Explicit confirm |
| `PATTERN_FEEDBACK_DISAGREE_PENALTY` | 0.15 | Explicit disagree |
| `PATTERN_CONTRADICTION_PENALTY` | 0.10 | Counter-evidence |

## Scoring model

```
confidence ≈ volume + consistency + data_quality
           + confirm_boosts
           − disagree_penalties
           − contradiction_penalties
           − days_since_last × decay_per_day
```

Clamped to [0, 1]. Status uses evidence thresholds above. Inactive if
stale beyond `PATTERN_INACTIVE_DAYS` or confidence collapses.

## Data sources (adapters)

| Domain | Source today | Notes |
|---|---|---|
| Mood | `mood_logs` | Ready |
| Academic | `marks` | Ready |
| Habits / journal-ish | `habit_events` | Titles only |
| Conversation themes | `user_insights` | Crisis rows excluded |
| Language | `users.preferred_language` | Ready |
| Meditation tools | `action_card_logs` TOOL_CARD | Partial |
| APM feedback | `apm_events` HELPFUL/NOT_HELPFUL | Consent-gated |
| Sleep / tasks / attendance writers | — | Empty adapters (no false patterns) |

## Consent

- `users.personalization_consent` must be true to **persist** or **retrieve**
  personalized patterns (same gate as APM).
- Consent **off**: no new pattern writes; retrieval returns empty context;
  stored rows are left in place but unread.
- Consent **revocation wipe**: `DELETE /api/memory` also deletes
  `user_patterns` and `pattern_evidence` for that user.

## Crisis safety

- Crisis keyword / flagged turns: no pattern detection reinforcement.
- Crisis `user_insights` rows are never used as evidence.
- Pattern context is not injected on crisis-looking user messages.
- Pattern layer never overrides crisis escalation paths.

## Privacy / isolation

Every query filters `user_id == authenticated_user_id`.  
Indexes are user-prefixed. Cross-user reads/writes are rejected by query scope.

## API

- `POST /api/patterns/feedback` — `{pattern_id, event_type, note?}`  
  Events: CONFIRM, DISAGREE, NOT_RELATED, HELPFUL, NOT_HELPFUL.
- Chat contracts unchanged; `{pattern_context}` is an internal prompt slot.

## Integration points

- `services/context.py` → `get_pattern_context`
- `prompts.py` → `{pattern_context}`
- `services/extraction.py` Task 4 → `run_pattern_detection`
- `database.py` → `ensure_pattern_indexes`

## Safety non-goals

Does **not** optimize for engagement, session length, or return frequency.  
Does **not** treat chat volume as improvement.  
Does **not** diagnose.  
Does **not** claim causation from co-occurrence.
