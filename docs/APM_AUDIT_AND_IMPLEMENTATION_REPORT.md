# APM Audit and Implementation Report

Engineering report for Zenark Adaptive Psychological Memory.
This is **not** clinical validation and **not** a production-readiness certificate.

# Executive Result

APM PARTIALLY IMPLEMENTED — GAPS CLOSED

The existing `services/apm.py` graph already stored triggers, latent
states, interventions, outcomes, recovery edges, decay, consent, crisis
exclusion, and prompt injection. It did **not** yet represent first-class
episodes, distinct intensity, explicit vs inferred on nodes, user
correction, extraction idempotency, sealed display labels, or
trigger→state retrieval for later turns without a recovery edge.

Those gaps were closed **inside** the current Mongo APM architecture.
No second memory database was added.

## Existing capabilities

- Node types TRIGGER / LATENT_STATE / INTERVENTION / OUTCOME / CONTEXT
- Edges TRIGGERS / EVOLVES_INTO / RECOVERED_BY / REINFORCES
- `occurrence_count`, first/last seen, temporal buckets
- `attributes.valence` and arousal on extract
- `effective_edge_score` decay
- Consent via `personalization_enabled`
- Extraction skipped for incomplete streams, unsafe classes, crisis
- `get_recovery_paths` + `{adaptive_memory_context}` in the system prompt
- Explicit HELPFUL / NOT_HELPFUL; STARTED/COMPLETED do not count as success
- Meditation ranking consumes recovery rates
- Proactive engine already queried APM
- Erasure of `apm_nodes` / `apm_edges` / `apm_events`
- Cross-user `user_id` + namespaced ids

## Missing capabilities (before this pass)

- No `apm_episodes` (turn-level episode structure)
- Intensity not distinct from arousal / risk / GDS
- No `source_kind` / explicitness on nodes
- No user correction / INVALIDATED status
- Observation events not idempotent (could double-count a retried turn)
- `display_label` stored in the clear
- Retrieval only returned `RECOVERED_BY` paths — a trigger without a
  successful intervention did not shape later replies
- Current-turn denial did not suppress stale APM
- `apm_episodes` not in erasure
- Allowlisted APM metrics missing

## Changes made

- Episodes: `apm_episodes` with valence, intensity, context bucket,
  explicitness, source hash, node ids
- `attributes.intensity` plus optional `APMObservation.intensity`
- `source_kind` explicit | inferred
- `apply_user_correction` + retrieval contradiction filter
- Observation nonce = sha256(user|session|message)
- Seal `display_label`; `open_text` on retrieve
- `get_relevant_associations` follows TRIGGERS edges into prompt context
- Erasure includes `apm_episodes`
- Metrics: `apm_extraction`, `apm_retrieval`, `apm_pattern_update`,
  `apm_outcome_update`, `apm_suppression`, `apm_correction`, `apm_erasure`
- Proactive retrieval skips INVALIDATED nodes and contradicted topics

## Data model

Unchanged collections plus `apm_episodes`. Nodes gained `status`,
`source_kind`, `attributes.intensity`. Events gained `execution_nonce`
on OBSERVATION.

## Extraction

`run_background_extraction` → `_extract_apm_observations` →
`persist_apm_extraction(..., message=)`. Still skipped for incomplete,
unsafe, crisis, and consent-off turns.

## Consolidation

Same canonical-label upsert. Recurrence increments only on a new
observation nonce. Pattern engine (`user_patterns`) remains separate
longitudinal detection and still reads APM feedback.

## Retrieval

Bounded: word-matched nodes (limit 8) + TRIGGERS edges + recovery paths
(limit 3). Consent and crisis still return empty. Stale denial of a
topic drops that association before the prompt.

## Response shaping

`get_adaptive_memory_context` now includes associations such as
“presentation associated with overwhelmed” so later turns can inform
without commanding a tool.

## Outcome learning

Unchanged contract: only explicit HELPFUL / NOT_HELPFUL updates success
counts. Extraction still must not treat assistant suggestions as relief.

## Safety

Unchanged gates plus blocked diagnostic/harmful labels on upsert.

## Consent

Unchanged `personalization_consent`. Immediate on next evaluate/turn.

## Privacy

`display_label` sealed. Prompt text uses opened short labels, never
`enc::` blobs. Graph ids remain in recovery lines for in-prompt card
payloads (existing TOOL_CARD contract); they are not a student-facing
API.

## Erasure

`apm_episodes` added to `_OWNED`. Tests: create → retrieve → erase →
empty context.

## Cross-user isolation

Queries remain `user_id` scoped. Tests assert User B family-context
retrieval does not contain User A’s presentation association.

## Tests

Extended `tests/test_apm.py` (episodes, recurrence, coping sequence,
later-turn retrieval, correction, consent/crisis, erasure, proactive
consume + correction). Encryption writer for `apm_nodes.display_label`.

Full tree (2026-09-30):

```
python -m pytest tests exam_buddy_guardrails/tests -q
458 passed
```

APM + encryption contracts in that run: `tests/test_apm.py` (18 tests)
plus `tests/test_encryption_contract.py` (display_label seal).

## Performance

Retrieval stays capped (8 nodes, 12 edges, 3 recovery paths, 4
association lines). No full-graph dump.

## Remaining limitations

- Live extractor quality still depends on Bedrock
- South-Indian native-script episode labels not separately certified
- Recovery card lines still include `edge_id` for action-payload copy
  (internal prompt, not public JSON)
- Not load-tested as a standalone service
- Not clinically validated
