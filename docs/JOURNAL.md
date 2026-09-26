# Journal context

`journal_entries` is the only journal store. The `journaling` package is the only writer. Chat, reports, and the pattern engine call that package. They do not query the collection on their own, and they do not copy entries into Graph RAG or adaptive memory.

## Schema

| Field | Meaning |
| --- | --- |
| `_id` | Mongo ObjectId |
| `user_id` | Authenticated chat identity |
| `mood` | One of 😊 😃 😐 😢, chosen by the person |
| `title` | At least 3 characters |
| `content` | At least 10 characters. Never rewritten by analysis |
| `tags` | Optional |
| `time_spent` | Optional seconds, default 0 |
| `is_favorite` / `favorited_at` | Optional |
| `timestamp`, `created_at`, `updated_at` | Server UTC timestamps |

A selected mood is kept as they set it. Inferred topics are not written back onto the entry.

## Write and read

`POST /journal/entry` calls `create_journal_entry`. The body `user_id` cannot target another person. The same title, content, and mood posted again within two minutes returns the original row.

Student reads, all scoped to the authenticated identity:

- `GET /journal/recent-entries`
- `GET /journal/entry/{entry_id}`
- `GET /journal/past-reflections`
- `GET /journal/calendar-data`
- `GET /journal/favorites`
- `GET /journal/stats`
- `GET /journal/monthly-mindfulness`

Lists return a short preview. The single-entry route returns that person's full text. Recent rows are ordered by `timestamp`, newest first.

Streamlit's New Journal form posts to `POST /journal/entry`. It does not write to Mongo itself.

## What the model sees

`fetch_user_context` adds `{journal_context}` for both `/chat/send` and `/chat/stream`. Defaults: 5 entries, 180 characters each, 1200 characters for the whole block (`JOURNAL_CONTEXT_LIMIT`, `JOURNAL_PREVIEW_CHARS`, `JOURNAL_CONTEXT_MAX_CHARS`).

Mood check-ins stay in `{recent_moods}`. Journal previews are separate.

The model is told to use a journal note only when the current message is still about that concern, and not to say it searched a database. Entries that contain acute crisis language are left out of this preview and out of pattern learning. The live chat crisis path is unchanged.

If the read fails, the block is `No journal entries available.` and the reply still generates.

## Patterns

Detection runs on the existing background pass, only with personalization consent, and never treats a crisis entry as mood evidence. Nothing from a journal is written into graph nodes or adaptive-memory edges.

| Level | Example |
| --- | --- |
| Fact | They saved an entry and selected 😢 |
| Observation | Recent selected moods are mostly 😢 |
| Pattern | A topic repeats, or a negative selected mood repeatedly overlaps shorter sleep, pending tasks, or lower marks |
| Hypothesis | It may be worth asking whether they see a connection |

Equal overlap in both directions is dropped. A hypothesis is not stored. Stored journal patterns use the same decay as other patterns.

Deleting adaptive memory removes derived patterns. It does not delete the journal entries the person wrote.

## Reports and meditation

A session report receives the preview plus any retrieved journal patterns. The conversation still drives the emotional reading. A journal pattern may add low mood as a secondary signal. It cannot outrank the report, and it does not map a sad entry to a fixed meditation.

## Indexes

`user_id`, `timestamp`, `(user_id, timestamp)`, `(user_id, is_favorite)`, and `tags`.
