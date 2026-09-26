# Personalized meditation

Zenark does not map a feeling to one fixed session. A practice is offered only when the current estimate is strong enough, the recording exists, and the effort fits. The same broad emotion can produce different practices for different people.

This is not a crisis pathway. Acute crisis language keeps the existing emergency response and does not produce a meditation card.

## Catalog

`meditation/data.py` is the session catalog: id, tab, title, description, category, and whether audio was authored. Helpers stay:

- `get_all_sessions()`
- `get_session_by_id()`
- `get_sessions_by_tab()`
- `get_sessions_by_category()`

Audio files live in `meditation/meditation audios/`. `get_audio_path(meditation_id)` checks the catalog flag, resolves `{id}.mp3` (top-level file first), and returns `None` when the file is missing. The app keeps working without that file.

`has_audio` on the built session means the file was found. `catalog_has_audio` keeps the original flag. Duration is estimated from the MPEG frame bitrate and file size (`duration_source: mp3_bitrate_estimate`). There is no blanket 10-minute default.

## Metadata

`meditation/metadata.py` is a separate, editable attribute table. Sessions missing from it stay `unreviewed` and are not candidates.

Reviewed entries start as `provisional`: a reading of the title and description, not a clinical validation. A session becomes `empirically_validated` only after at least 50 completed listens and an explicit helpful rate of at least 80% (helpful divided by helpful + not helpful). Completions with no vote do not count as positive. The source file is not rewritten; the promoted status is stored in `meditation_metadata_promotions` and overlaid at rank time. Both statuses stay eligible. Unreviewed sessions stay out. They carry PAD targets, latent-state tags, cognitive load, technique, tags, languages, and a user-facing reason. Friction is derived from measured duration:

| Band | Length |
| --- | --- |
| MICRO | under 2 minutes |
| LOW | 2–5 minutes |
| MEDIUM | 5–10 minutes |
| DEEP | over 10 minutes |

Most of the current recordings are longer than two minutes, so an overloaded moment prefers the shortest very-low-effort matches that actually exist. A missing MICRO file does not get invented.

## State estimate

Internal only. The user never sees these numbers.

PAD is valence, arousal, and dominance, each from -1 to +1, plus a confidence from 0 to 1. Latent labels such as `ANXIETY_HIGH` or `SLEEP_PREPARATION` are probabilistic cues, not diagnoses. The chat model is told not to quote them.

Distance:

```
sqrt(
  (target_valence - user_valence)^2
  + (target_arousal - user_arousal)^2
  + (target_dominance - user_dominance)^2
)
```

PAD match is `1 - distance / sqrt(12)`. It is one term in the score, not the whole decision.

Current-turn language outweighs background context. Sleep notes, mood logs, journals, tasks, academics, and attendance can raise a weak estimate. They do not outvote a clear message.

## Pipeline

A practice is not chosen during a chat turn. The person asks for a session report. That report is the only place a recommendation is produced.

1. Load the session transcript
2. The report model returns a summary plus one structured state: valence, arousal, dominance, latent states, and confidence
3. Crisis language in the transcript, or a crisis flag on the report, withholds a practice
4. Longitudinal patterns may add a secondary label. They cannot replace the report's top state or its PAD values
5. The ranker then uses that state with meditation history, explicit feedback, preferred language, and catalog metadata
6. Hard filter: unreviewed metadata, missing audio, friction, cognitive load, latent mismatch, regional language mismatch, unknown duration
7. Withhold if confidence or the top score is below the configured floor
8. At most one practice, returned on the report
9. Streamlit plays the real file and records start / complete / feedback

`POST /api/session/report` with the authenticated user and `session_id`. The response summary has no coordinates. The stored report keeps the internal state for audit.

High activation (anxiety, panic, overwhelm, or high arousal) keeps MICRO and LOW friction, and VERY_LOW cognitive load. A steadier or reflective estimate may include MEDIUM and DEEP. Nothing is forced when the filter comes back empty.

## Ranking

Weights are in `config.py`.

```
final =
    pad_weight * PAD match
  + latent_weight * latent-state match
  + 0.16 * friction match
  + 0.12 * cognitive-load match
  + personal_weight * personal success
  + 0.04 * time-of-day fit
  + language bonus (0.05, only when the session language matches)
  - repetition penalty (up to 0.18, faded by 72 hours)
  - uncertainty penalty (0.12 * (1 - confidence))
```

With attributable history: `pad_weight` is 0.28, `latent_weight` is 0.24, `personal_weight` is 0.16.

Cold start (no explicit HELPFUL or NOT_HELPFUL for this user, and no APM recovery rate): `personal_weight` is 0, and that 0.16 is moved onto state match — PAD +0.09 (0.37) and latent +0.07 (0.31). The rest of the scale stays put.

Why these weights: state and effort decide whether a practice is even a fit. Personal history can break a tie or prefer a known-useful practice, but it is not large enough to drag an old favorite into a clearly different state. A new user does not leave 0.16 of the score unused. Time of day and language are small. Repetition is full for 12 hours, then declines in a straight line to zero at 72 hours, so Monday's practice can be offered again on Friday. Uncertainty shrinks shaky estimates. Global popularity is not an input.

Personal success is 0 when there is no explicit HELPFUL or NOT_HELPFUL outcome. Started, completed-without-feedback, dismissed, opened, or time spent do not count. If the same outcome is also on an APM `RECOVERED_BY` edge, the ranker uses the higher of the two rates so it is not counted twice. An edge with zero explicit successes and zero explicit failures contributes nothing.

`MEDITATION_MIN_CONFIDENCE` (0.42) and `MEDITATION_MIN_SCORE` (0.45) are the withhold floors.

## APM

There is no second learning system. Explicit HELPFUL / NOT_HELPFUL feedback, when personalization consent is on, is written through the existing recovery-edge path (`apm_edges`, relation `RECOVERED_BY`) and `record_intervention_feedback`. A completion writes an `apm_events` row with event type `COMPLETED` on that same path. Completion increments the edge's completion count and does not count as success. Consent off still stores the execution for this user; it does not write APM. Catalog promotion reads `meditation_executions`, so a withheld consent does not hide an explicit vote from the 50 / 80% check.

## Executions

Collection: `meditation_executions`.

Statuses: `STARTED`, `COMPLETED`, `ABANDONED` (abandoned is reserved; the current buttons use start, complete, and feedback).

Feedback: `HELPFUL`, `NOT_HELPFUL`, `DISMISSED`. Dismissed is stored and is not a success or a failure in the score.

Each execution has its own `execution_nonce`. Start is idempotent on `(user_id, execution_nonce)`. Complete and feedback only touch a row owned by the authenticated user. A second feedback write does not replace the first.

Endpoints, all authenticated:

- `POST /api/meditation/start`
- `POST /api/meditation/complete`
- `POST /api/meditation/feedback`
- `POST /api/meditation/preview` — developer payload, includes a debug object

Chat cards do not include PAD, probabilities, confidence, or score breakdowns. Each card carries a stable `meditation_id`, `execution_nonce`, and `reason`. Streamlit sends those three together to `POST /api/meditation/start`, then the same nonce to complete and feedback. Pattern feedback stays on `/api/patterns/feedback` and is not used for meditation outcomes.

Deleting adaptive memory also deletes that user's meditation executions and pending offers.

## Chat

`/chat/send` and `/chat/stream` rank during generation and pass a `NO_MEDITATION` or `RECOMMEND_MEDITATION` block into the system prompt. The server attaches at most one meditation tool card, drops any meditation card the model invents, and skips the practice when a psychiatrist or crisis card is already on the turn.

## Streamlit

A recommended practice renders inside the conversation: title, minutes, a plain-language reason, the audio file, Start, This helped, and Not for me.

The sidebar section **Developer demo — meditation ranking** logs into a seeded demo user and shows the practice plus an expandable **Recommendation debug** panel. That panel is not part of the normal chat card.

## Demo data

```
python scripts/seed_meditation_demo.py
python scripts/seed_meditation_demo.py --reset
```

Five accounts, password `Zenark@123`:

| Email | Profile shape |
| --- | --- |
| ananya.rao@zenark.demo | Short sleep, high stress, short practices marked helpful |
| rohan.desai@zenark.demo | Stable sleep, longer body scans marked helpful |
| leela.nair@zenark.demo | Low mood and withdrawal, self-compassion marked helpful |
| ishaan.mehta@zenark.demo | Task backlog, focus practice marked helpful |
| sara.qureshi@zenark.demo | Evening wind-down, sleep body scan marked helpful |

The script prints the practice the ranker selects for each probe. Those ids are outputs, not a lookup table.
