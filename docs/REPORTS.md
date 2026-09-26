# Session reports

`reports/` is the only writer of the reading used for the next welcome. The collection is `session_reports`, one document per user and session.

A report is created when the person asks for one. Chat does not create it. The welcome does not read raw messages from the previous session.

## Stored reading

- `psychiatric_summary` — a short reading of the sitting, without quotes
- `events` — major situations the person shared. Each has `event_id`, `label`, and `resolved`
- `proposed_tasks` — concrete next steps. They are not on today's task list until the person adds one
- `psychiatric_metric` — session load from 1 (settled) to 10 (heavy). Not a diagnosis

The warm `summary` shown in the app stays separate from the welcome reading.

## Welcome

The opening turn receives the last few reports, not a transcript. Settled events are left out. If an event is still open, the welcome may ask once whether it is settled. It does not retell it.

## Settled events

An open event becomes `resolved: true` only when a later message confirms that event. A bare yes counts only when the previous reply named exactly one open event. A yes that does not point at an event changes nothing. Settled events are not asked about again.

## Tasks

`POST /api/reports/tasks/accept` with `session_id` and `task_id` copies that proposal onto today's `daily_tasks` list. Other proposals stay on the report. A user id in the body cannot target someone else.

Adding or completing a task is not treated as a helpful outcome.
