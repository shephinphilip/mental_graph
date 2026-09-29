# Proactive Question Implementation Report

Engineering report for Zenark’s in-conversation proactive question engine.
This is **not** a production-readiness certificate and **not** clinical
validation of JITAI, graph memory, or the conversational strategy.

## Files changed

### Added

| Path | Role |
|------|------|
| `services/proactive/__init__.py` | Package exports |
| `services/proactive/schemas.py` | Internal decision/status/trigger contracts |
| `services/proactive/eligibility.py` | Safety + escalation mapping onto existing classifiers |
| `services/proactive/receptivity.py` | Current-turn receptivity; unavailable-signal list |
| `services/proactive/trigger_engine.py` | Bounded APM/graph retrieval, decay, divergence |
| `services/proactive/question_generator.py` | Deterministic Mirror-With-Agency templates |
| `services/proactive/inner_council.py` | Deterministic four-lens review (reuses `services.inner_council`) |
| `services/proactive/validator.py` | One-question / diagnosis / internals / script gate |
| `services/proactive/store.py` | `proactive_questions` persistence, indexes, cooldown, nonce |
| `services/proactive/service.py` | Pipeline orchestrator, chat hook, outcome recording |
| `services/proactive/observability.py` | Allowlisted logs + in-process counters |
| `api/routes/proactive.py` | evaluate / pending / respond |
| `tests/test_proactive.py` | Engine, isolation, HTTP, privacy tests |
| `docs/PROACTIVE_QUESTION_ENGINE.md` | Architecture and product contract |

### Modified

| Path | Change |
|------|--------|
| `config/config.py` | `PROACTIVE_*` env-tunable thresholds |
| `services/telemetry.py` | Allowlisted proactive metric names |
| `schemas.py` | Public evaluate/respond/decision models |
| `services/graph.py` | Evaluate + stance inject in `generate_node`; `DELIVERED` after persist |
| `services/streaming.py` | Same hook on the SSE path |
| `api/router.py` | Mount proactive routes on `/api` and `/api/v1` |
| `db/indexes.py` | `ensure_proactive_indexes` |
| `services/erasure.py` | `proactive_questions` user-scoped delete |
| `docs/ENCRYPTION_DATA_MATRIX.md` | Sealed `proactive_questions.question` |
| `tests/test_encryption_contract.py` | Matrix + sealed writer |
| `tests/api/test_http_contract.py` | Mounted path assertions |

## Architecture integration

The LangGraph topology is unchanged:

`START → fetch_context → retrieve_graph_context → generate → format_output → END`

The engine runs **inside generate** (and the streaming equivalent) after the
existing Inner Council, before `format_system_prompt`. Failures never block
the companion reply.

Existing owners reused, not replaced:

- Chat: `services.graph` / `services.streaming`
- Memory: Mongo APM (`apm_nodes` / `apm_edges`) + bounded therapeutic `graph_nodes`
- Personalization consent: `services.apm.personalization_enabled`
- Safety: `services.safety_class.classify_message`, APM crisis terms, `RISK_HIGH_THRESHOLD`, escalation cases
- Language: `resolve_response_language` / `language_instruction`
- Validator: `services.response_validator.validate_reply`
- Inner Council: `services.inner_council.deliberate`
- Identity: bearer `authenticated_user_id` + `assert_owner`
- Telemetry: allowlist + `engagement_guard`
- Erasure: `_OWNED`

`evaluate_jitai_candidate` is **unchanged** (`outbound_enabled=False`). This
engine is the in-conversation JITAI path, not a push notifier.

## Data model

Collection `proactive_questions`, user-scoped.

Fields include: `user_id`, `event_id`, `execution_nonce`, `session_id`,
`trigger_type`, `source_node_ids` (internal only), `topic`,
`receptivity_state`, `confidence`, `risk_state`, `language`, `script`,
sealed `question`, `question_fingerprint`, `status`, `suppression_reason`,
timestamps, `expires_at`, `outcome`, `evidence_kind`.

Indexes: unique `(user_id, event_id)`, unique `(user_id, execution_nonce)`,
status timeline, expiry.

Question text is Fernet-sealed (`enc::`).

## APIs

| Method | Path | Behavior |
|--------|------|----------|
| POST | `/api/v1/proactive/evaluate` | Decision only; status `APPROVED` if allowed |
| GET | `/api/v1/proactive/pending` | Latest unexpired `APPROVED` question |
| POST | `/api/v1/proactive/respond` | Outcome; crisis excluded from APM reinforcement |

Compatibility mounts exist under `/api/proactive/*`.

Public bodies expose `decision`, `event_id`, `question`, `reason`, optional
`status`. They do not expose graph ids, scores, telemetry, or council notes.

## Safety integration

Mapped onto the existing classifier and `RISK_HIGH_THRESHOLD` (default 8.0).
No independent “8/10” constant. Crisis, self-harm, violence, abuse, sexual
exploitation/content, substance, misconduct, jailbreak, persistent distress,
and open escalation all suppress casual proactive questions.

## Consent integration

Existing `personalization` consent only. Off → no historical graph/APM
proactive personalization and no outcome writes. Immediate on next evaluate.

## Graph integration

Bounded APM retrieval with `effective_edge_score` decay + lookback window.
Optional user-scoped therapeutic graph fallback. Empty graph → no question.
Cross-user queries are filtered by `user_id`; tests assert user B’s labels
never appear for user A. Harmful labels are dropped.

## Receptivity model

`RECEPTIVE` / `NEUTRAL` / `LOW_RECEPTIVITY` / `HIGH_OVERLOAD` / `UNKNOWN`.
Uses current-turn language, existing overload markers, journal-context
presence already fetched for the prompt, and late-night clock **caution
only**. Typing, app opens, and other forbidden features are listed as
unavailable and rejected if passed in.

## Divergence logic

Minimizing current text + recent owned trigger/context → tentative
clarifying question. Compatible current-topic talk is not divergence.
Telemetry cannot produce an emotional conclusion.

## Inner council

Deterministic. Reuses `deliberate()` for risk; additional checks for warmth,
grounded wording, and no behavioral pressure. No four extra LLM calls.

## Validator

One `?`, no diagnosis, no internals, no pressure, `validate_reply`, ROMAN
script. One bounded soften retry.

## Dispatch

Chat inject → `DISPATCHED`. Assistant persist → `DELIVERED`. Evaluate-only
stays `APPROVED` until expiry or a later turn. A Mongo insert is not claimed
as delivered. No student push bus exists; none was invented.

## Tests

`python -m pytest tests exam_buddy_guardrails/tests -q`

**421 passed** (full tree: `tests/` + `exam_buddy_guardrails/tests`) at first implementation.

Proactive-specific (first pass): **24** tests in `tests/test_proactive.py` covering
eligibility, safety, consent, graph isolation, decay, divergence, one-question
voice, language/script, outcomes, expiry, idempotency, privacy, HTTP auth.

No existing tests were deleted or weakened.

## Known limitations

- No durable outbound scheduler or push delivery.
- `SEEN` is not inferred (no approved open/read telemetry).
- Standalone evaluate wording is English templates; chat/stream localization
  uses the existing language prompt.
- Receptivity cannot use typing or app-open patterns (not allowlisted).
- Outcome learning writes generic APM CONTEXT/OUTCOME labels, never an
  automatic `RECOVERED_BY` edge.
- Not load-tested as a separate service.

## NOT IMPLEMENTED

- Push notifications / cron JITAI outbound (APM stub remains disabled)
- New consent purpose
- Second safety classifier, language resolver, or graph database
- Covert substitutes for unavailable telemetry
- Four LLM council agents
- Claiming delivery from a database write alone
- Clinical validation or production-readiness sign-off

## Next steps

1. If product wants return-session questions without an open chat, add the
   smallest durable job that records `APPROVED` opportunities for
   `GET /pending` (still no fake “delivered”).
2. Done in the hardening pass: `/evaluate` localizes through
   `language_instruction`.
3. Tune `PROACTIVE_*` from live suppression/approval metrics, not from
   engagement counts.

---

# HARDENING PASS

Follow-up to close four gaps without redesigning the engine: final user-facing
response enforcement, `DELIVERED` linkage, Graph/APM retrieval reuse, and
`/evaluate` language/script consistency.

## What changed

### Final-response validation

`services/proactive/validator.py` `validate_final_response` runs on the
generated assistant reply, not only the pre-LLM candidate. It checks
meaningful question count (Unicode `?`, stacked “and” probes, double
imperatives — not punctuation counting alone), diagnosis, internals,
pressure, PII fishing, harmful guidance, ROMAN vs native script, and
English/Hinglish topic-intent overlap.

`services/proactive/delivery.py` `ensure_final_proactive_reply` applies that
gate with `PROACTIVE_FINAL_MAX_RETRIES` (default 1). Failure falls back to
an ordinary companion reply and `SUPPRESSED`. Success is required before
`DELIVERED`.

JSON chat (`generate_node`) and SSE (`stream_chat_graph`) both call this
after generation and before the client is committed to the text.

### Delivery semantics and message linkage

`DELIVERED` requires:

1. Final proactive validation pass
2. Persisted assistant `messages` row
3. `delivered_message_id` set on the opportunity

`commit_delivery` refuses empty message ids, duplicate ids that do not
match, and terminal statuses other than `DISPATCHED`/`APPROVED`.
`/evaluate` stays `APPROVED` and never claims delivery. `/respond` on an
undelivered event returns HTTP 400 (`not_delivered`). Outcomes still apply
only after delivery.

### Streaming

Non-proactive SSE still `astream`s. Proactive turns `ainvoke` the full
reply, validate, then emit `event: token`. Invalid multi-question text is
not sent. Ordinary fallback (if used) is what the client sees.

### Retrieval reuse

`retrieve_bounded_context` hydrates labels from the turn’s existing
`graph_context` and `adaptive_memory_context` first. Extra user-scoped APM
or therapeutic-graph queries run only when those strings cannot answer.
Isolation bounds are unchanged. Safe logs record reuse vs extra query and
latency; they do not log documents, journals, or chat bodies.

### `/evaluate` language contract

User-facing localized generation. Same `resolve_response_language` /
`language_instruction` as chat. Indian languages default to ROMAN on
opening/Latin input; native script when the current message is in native
letters. Chat/stream still inject the English source candidate so the
companion localizes in-turn.

## Files changed (this pass)

| Path | Role |
|------|------|
| `services/proactive/validator.py` | Final-response + meaningful-question checks |
| `services/proactive/delivery.py` | Bounded retry, suppress, `commit_delivery` |
| `services/proactive/localize.py` | Evaluate-path localization via existing instruction |
| `services/proactive/trigger_engine.py` | Context hydrate; extra query only if needed |
| `services/proactive/service.py` | Retrieval flags, localize on evaluate, delivery API |
| `services/proactive/store.py` | `delivered_message_id` |
| `services/proactive/schemas.py` | Topic + retrieval fields on `ProactiveResult` |
| `services/graph.py` | Final validation + delivery linkage |
| `services/streaming.py` | Buffer/validate-before-emit; delivery linkage |
| `api/routes/proactive.py` | 400 when respond-before-deliver |
| `config/config.py` | `PROACTIVE_FINAL_MAX_RETRIES` |
| `services/telemetry.py` | `proactive_validation`, `proactive_retry` |
| `tests/test_proactive.py` | Delivery-before-outcome; evaluate language mock |
| `tests/test_proactive_hardening.py` | Failure modes, security, integrity, stream |
| `docs/PROACTIVE_QUESTION_ENGINE.md` | Guarantee, delivery, stream, retrieval, evaluate |

## Tests added

Hardening file plus strengthened existing outcome/HTTP tests. Existing
names were not deleted. Outcome recording now requires a delivered
assistant `message_id` (correctness, not a weaker assertion).

Covered: one valid question → `DELIVERED`; two questions; ignored
instruction; diagnosis; telemetry; graph internals; pressure; bounded
retry success/exhaustion; invalid stream never emitted; duplicate
event; `/evaluate` never `DELIVERED`; consent off; crisis; linkage
invariants; erasure; ENGLISH / HINGLISH ROMAN / HINDI native (message-
selected) / MALAYALAM, TAMIL, TELUGU, KANNADA ROMAN on opening evaluate;
native-script validator samples for those languages.

Language tests mock `localize_question` (no live Bedrock). Native-script
evaluate for Malayalam/Tamil/Telugu/Kannada is validator-covered, not a
full evaluate round-trip.

## Final test count

`python -m pytest tests exam_buddy_guardrails/tests -q`

- Baseline (before this pass): **421 passed**
- Final: **451 passed**
- New: **30**

## Remaining limitations

- No durable outbound push/scheduler
- `SEEN` is not inferred
- Evaluate localization is LLM-backed; tests mock it
- Native-script `/evaluate` for south-Indian languages is not an
  end-to-end evaluate test (opening turns correctly resolve ROMAN)
- Intent-token overlap is enforced for ENGLISH/HINGLISH Latin/Roman, not
  for native-script bodies
- A `DISPATCHED` turn whose persist fails stays undispatched to the user
  (no duplicate delivery on retry)
- Not load-tested. Not clinically validated. Not production-certified.

## Decisions left unresolved

- Whether product later wants a durable job for return-session
  `APPROVED` prompts (`GET /pending` still works)
- Whether native-script evaluate should be tested live against Bedrock
  for every supported language

---

# PROACTIVE QUESTION HARDENING STATUS

HARDENING COMPLETE WITH LIMITATIONS

- Baseline tests: 421 passed
- Final tests: 451 passed
- New tests: 30
- Files changed: validator, delivery, localize, trigger_engine, service,
  store, schemas, graph, streaming, proactive routes, config, telemetry,
  test_proactive.py, test_proactive_hardening.py, both docs
- Remaining limitations: listed above
- Intentionally unresolved: outbound scheduler, live multi-language Bedrock
  evaluate, clinical/production certification

