# Caches

| Cache | Key | TTL | Source of truth | Invalidation | Failure behavior |
| --- | --- | --- | --- | --- | --- |
| Language preference | `user_id` | 60s | `users.preferred_language` | write via `/api/language`; process restart | Used **only** when the DB read raises. Not a second store. |
| Settings | process singleton | process lifetime | environment / `.env` | restart | Missing required secrets log a warning; LLM may fail later |
| Meditation catalog | in-process metadata | process lifetime | `meditation/data.py` + files | deploy | Ranker returns no card |
| Rate-limit windows | path + auth suffix | 60s sliding | n/a (control plane) | expire | Each replica has its own counters |
| LLM circuit | process | `LLM_CIRCUIT_RESET_SECONDS` | n/a | success or reset | Open only when `APP_ENV` is production/staging |
| School dashboard bundle | `dashboard:bundle\|{school_key}\|{year}\|{from}\|{to}` | 45s | users, marks, mood, sleep, patterns, risk turns | TTL only; key always includes the school | Miss rebuilds from Mongo. Process-local, so replicas do not share it |

Do not cache raw conversations or journal bodies.
