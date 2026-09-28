# School dashboard API

Base path: `/api/v1/dashboard`

The dashboard is for one school at a time. It reads the collections Zenark already stores and adds a few collections for staff actions that did not exist (intervention plans, inbox, report jobs, settings). It does not copy the sample numbers from the dashboard HTML.

## Authentication

Send `Authorization: Bearer <access_token>` from `POST /api/v1/auth/login`.

| Situation | Status |
| --- | --- |
| Missing or invalid token | 401 |
| Student, teacher, generic `staff`, or `psychiatrist` | 403 |
| Dashboard role with no `school_id` and no `school` | 403 |
| Inactive account | 403 |
| Valid principal, `school_admin`, `admin`, or `counselor` | proceeds |

Teachers are listed by the dashboard. They cannot call it. A counselor can.

## School isolation

```text
Bearer token → user id → users document → tenant key
```

The tenant key is:

- `id:<school_id>` when the account has `school_id`
- otherwise `name:<exact school string>`

A name-only account does not see rows that already have a `school_id`. An id-scoped account does not see name-only rows. Query parameters named `school_id` are ignored.

Every student, teacher, class, intervention, notification, and report lookup is checked against that key. A resource in another school is **404**, so the response does not confirm that it exists.

Class ids, grade ids, and subject ids are slugs of stored labels (`10` → `10`, `Physics` → `physics`). They are not a separate class or subject collection.

## Response envelope

Success:

```json
{
  "success": true,
  "data": {},
  "meta": {
    "request_id": "…",
    "page": 1,
    "limit": 25,
    "has_next": false,
    "next_cursor": null,
    "total": 0
  }
}
```

`page`, `limit`, `has_next`, `next_cursor`, and `total` appear on list routes. Cursor pagination is ordered by the stable id (students, teachers) or by timestamp plus id (notifications, reports). `limit` is 1–100, default 25. An invalid cursor is 400.

Errors use the existing envelope (`success: false`, `error.code`, `error.message`, `detail`). Validation failures are **400**, which is how this API already reports schema errors. Stack traces, connection strings, and model credentials are not returned.

| Status | When |
| --- | --- |
| 400 | Bad id, bad `academic_year`, `from` after `to`, unknown JSON fields |
| 401 | Not signed in |
| 403 | Wrong role, no school, AI insights turned off |
| 404 | Other school, or the class / student / teacher / report is not in this school |
| 409 | Report preview or download before the job is `ready` |
| 429 | Process rate limit. Assistant is capped at 20/minute. Report routes use the report limit. |
| 500 | Unexpected failure. Body is the generic internal error. |

`academic_year` looks like `2025-26` (April–March). `from` and `to` are ISO timestamps.

## Shared filters

`academic_year`, `grade_id`, `class_id`, `subject_id`, `from`, `to` apply to overview, class routes, risk, analytics, and teachers where noted. The academic year filters **marks**. Date bounds filter marks, moods, and sleep. Grade and class filter the roster.

## Metric calculation

Numbers come from stored rows. A missing source is `null` with `available: false` and a `reason`.

| Metric | Source | Rule |
| --- | --- | --- |
| Student count | `users` in the tenant, excluding staff and teacher roles | |
| Teacher count | `users.roles` contains `teacher` | |
| Academic index | `marks` | Mean of each student's latest percentage per subject, then the mean of those |
| Mark percentage | `marks.percentage`, or `marks / total_marks` | Same helper as chat marks context (`services/marks.py`) |
| Growth delta | `marks` | Mean of per-subject (last − first) where the subject has at least two scores. Cutoffs ±8 match the existing trend language |
| Mental index | `mood_logs.score` (1–10) | Mean × 10. Notes are not read |
| Physical index | `sleep_logs.total_duration_minutes` | 100 at 8 hours, minus 12.5 per hour away from 8 |
| School health index | physical and mental | Mean of whichever of those exists. Social connection is not included |
| Wellness trend | mood scores | Last 14 days minus the 14 days before, only when both windows have at least two scores |
| Attendance | `users.attendance_percentage` or `student_psychological_profiles.attendance.attendance_percentage` | Mean of students who have a value |
| Risk | `users.current_risk_level`, `user_risk_turns`, `user_patterns` | See below |
| Passing rate | — | Not calculated. Marks have no pass mark |
| Parent NPS | — | No survey collection |
| Growth SGP | — | No normative cohort. `growth_delta` is returned instead |
| Teacher rating | — | No rating collection. A client `rating` field is rejected |
| Teacher performance score | — | Marks have no `teacher_id` |
| Concept / micro-topic mastery | — | Marks are per subject |
| Regional benchmark | — | No benchmark collection. `benchmark` is null |
| Assignment completion | — | `daily_tasks` are personal wellness tasks, not classwork |

Overview `insight.text` is a sentence built from those aggregates. `llm_used` is false. The assistant route is the only LLM call.

### Risk

The dashboard does not score message text again.

1. `users.current_risk_level`: `crisis` / `critical` / `high` → critical; `elevated` / `moderate` / `at_risk` → at risk; `watch` / `emerging` → watch; `low` / `typical` → none.
2. Latest `user_risk_turns` row, using the same bands as `services/risk_assessor.py`: crisis keywords or score ≥ 8 → critical; score ≥ 5 → at risk.
3. The more severe of (1) and (2) wins.
4. An `EMERGING` or `ESTABLISHED` pattern at or above `PATTERN_RETRIEVAL_MIN_CONFIDENCE` can raise a student with no other band to **watch**. It cannot raise them to critical.

The concern field is a category (`stored_risk_level`, `risk_turn`, or `pattern_type`). Pattern descriptions are not returned.

### Performance quadrant

Id: `marks_delta_quadrant_v1`. Isolated in `dashboard/metrics.py`.

A student needs two stored percentages in a subject. Otherwise they are `unclassified`.

| Bucket | Rule |
| --- | --- |
| Stars | Latest mean ≥ 75 and delta > −8 |
| Plateaued | Latest mean ≥ 75 and delta ≤ −8, or a middle result that is not climbing and not critical |
| Climbers | Latest mean < 75 and delta ≥ 8 |
| Critical | Latest mean < 45, or latest mean < 75 and delta ≤ −8 |

75 and 45 are the strength and struggle lines already used when a student profile is built. ±8 are the improve and decline cutoffs already used for marks context. Each bucket returns at most 50 students; `count` is the full count and `truncated` is true when the list was cut.

## Real-time events

`GET /api/v1/dashboard/events` is a `text/event-stream`. The first chunk is the comment `: connected`. Keepalives are `: keepalive` every 15 seconds.

Events published today, and only to subscribers of that school key:

| Event | Producer |
| --- | --- |
| `notification_created` | Inbox writes |
| `intervention_updated` | Create or patch a plan |
| `report_ready` | Background report job finishes |

The bus also accepts `student_risk_changed`, `attendance_updated`, `academic_score_updated`, `wellbeing_updated`, `class_metric_updated`, and `school_metric_updated`. Chat, marks, and attendance writers do not call it yet, so those names are not emitted automatically. There is no Redis pub/sub in this deployment: one API process has one bus. A second worker will not see the first worker's subscribers.

Payload shape:

```json
{
  "event": "intervention_updated",
  "school_id": "name:Riverdale School",
  "intervention_id": "int_…",
  "student_id": "stu_…",
  "status": "planned",
  "timestamp": "2026-09-27T17:30:00Z"
}
```

`school_id` is overwritten with the publisher's school key. Notification bodies are not put on the stream.

## Report jobs

`POST /reports` inserts a job and returns **202**. A background task then sets `generating`, then `ready` or `failed`, stores a JSON body, emits `report_ready`, and (unless `report_generation_alerts` is false) writes an inbox row.

Types: `parent`, `board`, `wellbeing`. Scope: `school`, `grade`, `class`, `student`. Grade, class, and student scopes require the matching id, and that id must belong to the school.

Board and school-level parent reports are aggregates. Names are removed from top performers and teachers. A parent report with `scope: student` includes that one student's percentage, attendance, risk band, and quadrant. Journals are never included. `include_charts: true` is stored; `charts` stays null because there is no chart renderer. `include_ai_insights` attaches the rules insight, not a model call.

Preview and download return **409** until `status` is `ready`.

## Assistant

`POST /assistant` checks `ai_insights`, loads the same aggregates as the overview, and sends that JSON plus the question. Student names, mood notes, journals, and conversations are not in the prompt. The question is passed through `anonymize_text`. If the model client raises, the route still returns 200 with `llm_used: false` and a rules sentence. The model key stays on the server.

## Settings

Stored on `dashboard_settings`, one row per staff member per school.

| Flag | Effect today |
| --- | --- |
| `report_generation_alerts` | Inbox item when that person's report is ready |
| `ai_insights` | `false` makes `POST /assistant` return 403 |
| `at_risk_alerts` | Stored only. Chat risk turns are not copied into this inbox |
| `weekly_digest` | Stored only. No digest worker or mailer is configured |

## Caching

Overview, class, risk, and analytics share one in-process bundle cache:

```text
dashboard:bundle|{school_key}|{academic_year}|{from}|{to}
```

TTL is 45 seconds. At most 64 entries. The key always includes the school. Notifications, settings, and student profiles are not cached. Replicas do not share the cache.

Reads are one query per collection for the school's student ids (`$in` chunks of 500), with projections that drop mood notes, pattern descriptions, and parent contact fields. Caps: 5,000 users, 20,000 marks, 20,000 signal rows. `sample_limits` on the overview says which cap was hit. The latest risk turn per student uses a `$sort` + `$group` pipeline when the driver implements `aggregate`.

## Collections

Reused: `users`, `marks`, `mood_logs`, `sleep_logs`, `user_patterns`, `user_risk_turns`, `meditation_executions`, `student_psychological_profiles` (attendance percentage only).

Created, because nothing equivalent existed:

| Collection | Purpose |
| --- | --- |
| `dashboard_interventions` | Staff plans. Profile "interventions" are meditation helpfulness and are not reused |
| `dashboard_notifications` | Staff inbox. `consultation_notifications` is the clinical queue for one student |
| `dashboard_reports` | Async report jobs and JSON bodies |
| `dashboard_settings` | Per-staff flags |
| `dashboard_audit` | Actor, action, resource id. Message bodies are not stored here |
| `dashboard_teacher_actions` | Messages, reviews, support plans. No rating column |

Not created: `dashboard_metrics` (derived on read), `dashboard_benchmarks` (no source), `dashboard_events` (live bus only).

## Indexes

Created from `dashboard/indexes.py` during startup:

| Collection | Key | Unique |
| --- | --- | --- |
| `users` | `school_id`, `isActive` | no |
| `users` | `school`, `isActive` | no |
| `marks` | `student_id`, `exam_date` | no |
| `marks` | `student_id`, `subject`, `exam_date` | no |
| `dashboard_interventions` | `school_key`, `intervention_id` | yes |
| `dashboard_interventions` | `school_key`, `student_id`, `updated_at` | no |
| `dashboard_notifications` | `school_key`, `recipient_user_id`, `read`, `created_at` | no |
| `dashboard_reports` | `school_key`, `report_id` | yes |
| `dashboard_reports` | `school_key`, `created_at` | no |
| `dashboard_settings` | `school_key`, `user_id` | yes |
| `dashboard_audit` | `school_key`, `created_at` | no |
| `dashboard_teacher_actions` | `school_key`, `teacher_id`, `created_at` | no |

Mood, sleep, pattern, and risk-turn indexes already existed on `user_id`.

## Endpoint reference

All routes need the bearer token and a dashboard role. Path ids are 1–80 characters: letters, digits, `_`, `.`, `:`, `-`.

### GET /context

Who is signed in, which school, which role, and the permission names. Does not return diagnoses, journals, or memory.

### GET /academic-years

`years` lists values found on marks (stored `academic_year`, otherwise the April–March year of `exam_date`). `calendar_year` is today's school year and is labeled `source: calendar` so it is not mistaken for stored data.

### GET /grades

`{ "grades": [{ "grade_id", "student_count" }] }` from the numeric part of each student's class label.

### GET /classes

Query `grade_id`. `{ "classes": [{ "class_id", "label", "grade_id", "student_count" }] }`.

### GET /subjects

Query `grade_id`, `class_id`. Distinct `marks.subject` values for those students.

### GET /overview

School, student and teacher counts, school health, academic health, growth, risk counts, top five performers by latest percentage, teacher cards, rules insight, and `unavailable`.

Example:

```json
{
  "success": true,
  "data": {
    "school": {"id": "name:Riverdale School", "name": "Riverdale School", "board": "CBSE"},
    "counts": {"students": 4, "teachers": 1},
    "academic_health": {"index": 63.25, "passing_rate": null, "passing_rate_available": false},
    "school_growth": {"parent_nps": {"value": null, "available": false}},
    "risk": {"total_students": 4, "critical": 1, "at_risk": 0, "watch": 1, "none": 2}
  },
  "meta": {"request_id": "…"}
}
```

### GET /classes/{class_id}/overview

404 if this school has no students in that class. Returns class label, academic index, `growth_sgp` (unavailable), `growth_delta`, quadrant **counts**, wellness trend, subject strengths (≥ 75) and weaknesses (< 45), up to 15 students needing attention, 14-day wellness engagement, and attendance. Assignment completion is null.

### GET /classes/{class_id}/performance-quadrant

```json
{
  "stars": {"count": 1, "students": [{"student_id": "stu_star", "name": "Star Student", "latest_percentage": 88, "growth_delta": 8}], "truncated": false},
  "plateaued": {"count": 1, "students": []},
  "climbers": {"count": 1, "students": []},
  "critical": {"count": 1, "students": []},
  "unclassified": 0,
  "method": {"id": "marks_delta_quadrant_v1"}
}
```

### GET /classes/{class_id}/students

Paginated roster: id, name, grade, latest percentage, severity, quadrant.

### GET /classes/{class_id}/subjects/{subject_id}/overview

404 if the class or the subject is not in this school. Returns subject, matched teacher (only when a teacher user lists both the subject and the class), class average, school average, weekly trend, and students under 45 or declining in that subject. Feedback, rating, concept mastery, and micro-topics are unavailable.

### GET /students/{student_id}/profile

Identity, per-subject latest and delta, attendance, mood index, average sleep hours, risk band, pattern categories, and recent activity kinds (`mood_check_in`, `assessment`, `meditation_completed`, `sleep_log`) with timestamps. No note, journal, or conversation text. `concern_recorded` is a boolean.

### GET /students/{student_id}/interventions

Plans for that student. 404 if the student is not in the school.

### POST /students/{student_id}/parent-contact

```json
{ "message": "Please call the school office.", "reason": "attendance" }
```

Response: `status: recorded`, `delivered: false`, `parent_on_file` boolean. The parent address is not returned. An audit row is written.

### POST /students/{student_id}/notify-counselor

```json
{ "message": "Please review this student this week." }
```

Creates one inbox item per other counselor in the school and a copy for the caller. `counselors_notified` is 0 when none are on file; the request is still audited.

### GET /risk/summary

`total_students`, `critical`, `at_risk`, `watch`, `none`.

### GET /risk/students

Query `severity` (`critical`, `at_risk`, `watch`) plus the shared filters and pagination. Without `severity`, only students who have a band are returned. Fields: student id, name, severity, concern category, last event time, grade, class.

### POST /interventions

```json
{
  "student_id": "stu_critical",
  "title": "Physics recovery plan",
  "plan": "Short check-ins after the next unit test.",
  "status": "planned",
  "assignee_user_id": "counsel_river"
}
```

201. Status is `planned`, `in_progress`, `completed`, or `cancelled`. The assignee must be a dashboard role or teacher in this school. Emits `intervention_updated`.

### GET /interventions/{intervention_id}

404 outside the school.

### PATCH /interventions/{intervention_id}

Any of `title`, `plan`, `status`, `assignee_user_id`. Empty body is 400.

### GET /teachers

Paginated. Optional `subject_id` and `class_id` keep only teachers whose user document lists that subject or class. Rating and performance are null.

### GET /teachers/{teacher_id}

The card plus the latest stored actions. 404 for another school or for a student id.

### POST /teachers/{teacher_id}/messages

```json
{ "message": "Can we review the lab plan?" }
```

201. Stores a teacher action and an inbox row addressed to that teacher.

### POST /teachers/{teacher_id}/reviews

```json
{ "notes": "Focus on lab explanations.", "focus_areas": ["labs"] }
```

`rating` is rejected (400). The stored rating is null.

### POST /teachers/{teacher_id}/support-plan

```json
{ "summary": "Pair planning for the next unit.", "focus_areas": ["planning"] }
```

### GET /analytics/grade-trends

`{ "grades": [{ "grade_id", "periods": [{ "period": "2026-W24", "average", "assessments" }] }] }`.

### GET /analytics/subject-performance

`{ "subjects": [{ "subject_id", "subject", "average", "growth", "trend", "students" }] }`. `trend` is `improving`, `declining`, `stable`, or `insufficient_data`.

### GET /analytics/wellbeing

Mental index, physical index, mood trend, risk counts. No per-student notes. Social connection is unavailable.

### GET /analytics/comparative

```json
{
  "available": false,
  "reason": "No regional, board, or peer-school benchmark collection is stored.",
  "school": {"academic_index": 63.25, "student_count": 4},
  "benchmark": null
}
```

The school block is real. The benchmark is not filled in.

### GET /notifications

Query `unread_only`, `page`, `limit`, `cursor`. A row is visible when its school matches and either `recipient_user_id` is the caller or the audience intersects the caller's roles.

### POST /notifications/{notification_id}/read

### POST /notifications/read-all

Returns `{ "updated": N }` for rows the caller can see.

### GET /events

SSE. See the events section. 401 without a token. Do not buffer the response.

### POST /reports

202:

```json
{ "success": true, "data": { "report_id": "rep_…", "status": "queued", "type": "board" }, "meta": { "request_id": "…" } }
```

### GET /reports

Jobs for this school, without the body.

### GET /reports/{report_id}

Status, timestamps, and a safe error string if generation failed.

### GET /reports/{report_id}/preview

`summary` and `insight` once ready.

### GET /reports/{report_id}/download

`application/json` attachment. 404 for another school. 409 while queued.

### POST /assistant

```json
{
  "message": "Why is risk up in grade 10?",
  "context": {"grade_id": "10", "class_id": null}
}
```

```json
{
  "success": true,
  "data": {
    "answer": "…",
    "llm_used": true,
    "school_id": "name:Riverdale School",
    "context": {"grade_id": "10", "class_id": null, "subject_id": null, "academic_year": null}
  }
}
```

### GET /settings

Defaults until the first patch: all four flags true, `persisted: false`, plus a `delivery` object that says which flags are only stored.

### PATCH /settings

Any subset of the four booleans. Returns the saved row with `persisted: true`.

## What the UI cannot show from real data yet

- Official passing rate and board cutoff
- Parent NPS
- Social connection score
- Student growth percentile (SGP)
- Teacher rating and student feedback
- Teacher performance attributed to a person
- Concept mastery and micro-topic health
- Assignment completion
- Regional or peer benchmarks
- Rendered chart images inside reports
- Delivered parent messages (no messaging provider; the request is only recorded)
- Automatic at-risk inbox items from chat
- Weekly digest email
- Live `student_risk_changed` / attendance / marks events until those writers publish on the school bus
- A shared event bus across more than one API process

Class, grade, and subject ids are derived from the strings already stored on users and marks. A homeroom collection, a timetable, and a teacher-to-mark link are not in the database, so a teacher appears on a subject page only when their user document lists both `subjects` and `classes`.
