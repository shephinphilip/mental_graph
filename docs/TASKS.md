# Daily tasks from a session report

`daily_tasks` is the only task store. One document per person per UTC date. A session report may append tasks. It never replaces the day's list.

Chat does not create tasks. Tasks are proposed only when the person asks for a session report.

## Document

```
user_id, date, tasks, created_at
```

Each task has `id`, `title`, `description`, and `completed`. Optional fields: `is_custom`, `is_deleted`, `source` (`REPORT`, `CUSTOM`), and `source_session_id` for a report task. The task screen does not show source or session ids.

Generated ids look like `task_` plus 8 hex characters. Custom ids look like `custom_` plus 8 hex characters.

## Report flow

The report model returns the summary, the session state, and `tasks` (zero to three). The backend drops empty titles, titles over 80 characters, descriptions over 240 characters, vague lines such as "study harder", crisis wording, and titles that match a task already on the day, including completed and deleted ones. Anything past three is dropped. The valid ones are appended.

Asking for the same session report again returns those tasks and does not append a second copy.

If the write fails, the report is still returned with `task_persistence: "failed"` and the proposals that were not saved. A crisis conversation saves the report and writes no tasks.

Creating or completing a task is not treated as a helpful outcome in adaptive memory.

## Existing routes

All of these use the authenticated identity. A user id in the path or body cannot target someone else.

- `GET /api/report_card/tasks/{user_id}`
- `POST /api/report_card/tasks/complete`
- `POST /api/report_card/tasks/custom`
- `PATCH /api/report_card/tasks/custom/{user_id}/{task_id}`

The GET opens today's document if it is missing. It does not rewrite tasks that are already there. Deleted tasks are hidden. Completion still goes through the complete route.

## Context

The next chat can see the three most recent day documents, with pending and completed labels, through `{task_context}`. Deleted tasks are left out.
