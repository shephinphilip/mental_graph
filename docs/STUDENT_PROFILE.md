# Derived student profile

`student_psychological_profiles` holds one document per user. It is a compact summary for Zenark's chat context. Source collections stay the source of truth.

Chat reads that one document. It does not scan journals, sleep logs, marks, or messages on each turn. A rebuild runs in the background after chat extraction and after journal, sleep, mood, habit, task, meditation, language, and session-report writes, and on demand via `POST /api/memory/consolidate`.

## What is stored

Identity comes from `users`. Sleep hours come from `sleep_logs`. Journal themes come from topic tags and the mood the student selected, not an inferred mood. Marks come from `marks` when rows exist. Attendance comes from `users.attendance_summary` or `users.attendance_data` when that value is numeric or includes an explicit percentage. Meditation lists are completed `meditation_executions` and explicit `HELPFUL` / `NOT_HELPFUL` feedback. Session continuity uses the latest 10 `session_reports`, not message transcripts.

There is no separate attendance collection and no marks API. If those fields are missing, the profile stores null or an empty string. It does not invent a percentage, a trend, or an absence reason.

## Scores

Each domain is 1–10 or null, with confidence, evidence count, and a trend (`increasing`, `decreasing`, `stable`, `insufficient_data`). A score is written only when at least three pieces of evidence exist. Confidence is `min(1, evidence_count / 8)`. These are internal pattern estimates, not diagnoses.

Overall distress is the average of domains that already have a score, and only when at least two domains qualify.

## Consent and deletion

`users.personalization_consent` gates narrative personalization. When it is off, chat context keeps preferred language and states that scores, journal themes, intervention learning, and conversation summaries are not used. Explicit account fields such as a stored chief concern are not turned into a diagnosis.

`DELETE /api/memory` also deletes the derived profile. It does not delete journal entries, sleep logs, marks, attendance, or chat messages.

## Safety

The live crisis check overrides the profile. A stored "no crisis flag" line is not shown to the model. Historical crisis wording in the profile is labeled as stored, not as the current decision.

## Security

`POST /api/memory/consolidate` and `GET /api/memory/profile` use the authenticated user only. A client-supplied user id is not an owner.

The frozen contract treats this collection as a **derived compact summary**, consent-gated, with source collections remaining the source of truth. It does **not** require the profile document to be Fernet-sealed, and this change does not invent that policy.

Stored conversation snapshots use already-plaintext event labels and task titles. Sealed `session_reports` summaries are not copied, not decrypted into a new plaintext field, and not stored as `enc::` fragments. Raw chat and journal bodies are not copied. Owner APIs remain consent-gated.
