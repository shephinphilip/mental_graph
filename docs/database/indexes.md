# Database indexes

Startup calls `db.indexes.ensure_all_indexes`, which delegates to each
domain's existing `ensure_*_indexes`. Indexes are not recreated blindly;
each module already uses named, idempotent `create_index` calls.

| Collection | Fields | Unique | Purpose / query | Source | Production |
| --- | --- | --- | --- | --- | --- |
| users | email | yes | login / signup | `database` / `db.indexes` | required |
| users | user_id | yes | auth identity | `db.indexes` | required |
| messages | session_id, user_id, created_at, seq | no | history + resume | `services/chat_history.py` | required |
| messages | session_id, user_id, idempotency_key | yes (partial string) | retry-safe writes | `services/chat_history.py` | required |
| messages | session_id, user_id, message_kind | yes (welcome only) | one welcome per session | `services/chat_history.py` | required |
| graph_nodes | user_id, node_id | yes | Graph RAG node upsert | `services/mongo_graph.py` | required |
| graph_nodes | user_id, node_type / name | no | type/name lookup | `services/mongo_graph.py` | required |
| graph_relationships | user_id + edge fields | yes on full edge | Graph RAG edges | `services/mongo_graph.py` | required |
| apm_nodes | user_id, node_id | yes | APM node upsert | `services/apm.py` | required |
| apm_edges | user_id, edge_id | yes | APM edge upsert | `services/apm.py` | required |
| apm_events | user_id, execution_nonce, event_type | yes (partial) | feedback idempotency | `services/apm.py` | required |
| user_patterns | user_id, fingerprint / pattern_id | yes | pattern identity | `services/patterns/store.py` | required |
| pattern_evidence | user_id, event_key | yes | evidence idempotency | `services/patterns/store.py` | required |
| user_risk_turns | user_id, created_at | no | risk window | `services/patterns/store.py` | required |
| sleep_logs | user_id, created_at / date | no | recent + history | `sleep/indexes.py` | required |
| journal_entries | user_id, timestamp | no | recent / calendar | `journaling/indexes.py` | required |
| daily_tasks | user_id, date | yes | one day list | `tasks/indexes.py` | required |
| session_reports | user_id, session_id | yes | one reading per session | `reports/indexes.py` | required |
| student_memories | user_id, key | yes | fact upsert | `student_memory/indexes.py` | required |
| mood_logs | user_id, logged_at / created_at | no | recent + patterns | `tracking/indexes.py` | required |
| mood_logs | user_id, client_event_id | yes (partial string) | offline retry | `tracking/indexes.py` | required |
| habit_events | user_id, habit_id | yes | habit identity | `tracking/indexes.py` | required |
| meditation_executions | user_id, execution_nonce | yes | start idempotency | `services/meditation/service.py` | required |
| psychiatric_evaluations | userId, evaluation_timestamp | no | latest decision | `consultation/indexes.py` | required |
| consultation_notifications | userId, read, created_at | no | unread list | `consultation/indexes.py` | required |

**Not created by the API:** `marks` indexes exist only in `scripts/seed_dummy_data.py`.

**Query bounds:** list endpoints clamp `days` to `MAX_LIMIT` (default 90).
Journal readers already limit internally. Chat history is capped by
`MAX_HISTORY_MESSAGES`. Graph traversal is capped by `GRAPH_TRAVERSAL_DEPTH`.
