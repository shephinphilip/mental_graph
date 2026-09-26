# Structured student memory

`student_memory/` owns `student_memories`: one row per user per fact.

## Where facts come from

The session report model returns `facts` alongside the summary, events, and tasks. Each has `fact`, `category`, and `importance` (0 to 1). Categories: `ACADEMIC`, `FAMILY`, `SOCIAL`, `HEALTH`, `SLEEP`, `COPING`, `GOAL`, `PREFERENCE`, `EVENT`, `OTHER`.

Kept: short, durable, plain statements. Dropped: quotes, moods of the day, anything that reads as a diagnosis, duplicates.

Writes happen only when `personalization_consent` is on. Crisis sessions write nothing. Chat turns do not write facts.

## Row

```
memory_id, user_id, fact, key, category, importance, source_sessions,
archived, created_at, last_confirmed_at
```

A fact seen again is confirmed: importance rises a little and the session is added to `source_sessions`. It is not inserted twice.

## Decay and retrieval

Effective importance is stored importance minus `MEMORY_DECAY_PER_DAY` per day since last confirmed. Facts below `MEMORY_MIN_IMPORTANCE` are left out of the prompt. The top `MEMORY_CONTEXT_LIMIT` facts are appended to the `{user_memory}` block as `STUDENT FACTS`, each tagged strong, moderate, or faint.

## Consolidation

`consolidate_student_memory()` archives faded facts and rewrites `users.memory_summary` and `users.key_takeaways` from the strongest ones. Run it weekly or monthly:

```
python scripts/consolidate_memory.py           # all users with facts
python scripts/consolidate_memory.py usr_abc   # one user
```

`POST /api/memory/consolidate` runs it for the signed-in user. `DELETE /api/memory` removes the user's facts along with adaptive memory and patterns.
