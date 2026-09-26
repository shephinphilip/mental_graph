# Caches

| Cache | Key | TTL | Source of truth | Invalidation | Failure behavior |
| --- | --- | --- | --- | --- | --- |
| Language preference | `user_id` | 60s | `users.preferred_language` | write via `/api/language`; process restart | Used **only** when the DB read raises. Not a second store. |
| Settings | process singleton | process lifetime | environment / `.env` | restart | Missing required secrets log a warning; LLM may fail later |
| Meditation catalog | in-process metadata | process lifetime | `meditation/data.py` + files | deploy | Ranker returns no card |
| Rate-limit windows | path + auth suffix | 60s sliding | n/a (control plane) | expire | Each replica has its own counters |
| LLM circuit | process | `LLM_CIRCUIT_RESET_SECONDS` | n/a | success or reset | Open only when `APP_ENV` is production/staging |

Do not cache raw conversations or journal bodies.
