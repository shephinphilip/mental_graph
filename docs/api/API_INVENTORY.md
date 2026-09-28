# API inventory

Generated from the live FastAPI router. Compatibility paths are the same handlers remounted without /api/v1.

| Method | Endpoint | Feature | Auth | Streaming | Status |
|---|---|---|---|---|---|
| POST | /api/v1/auth/login | Authentication | No | No | Active |
| POST | /api/v1/auth/signup | Authentication | No | No | Active |
| POST | /api/v1/chat/send | Chat | Yes | No | Active |
| GET | /api/v1/chat/session/{user_id}/resume | Chat | Yes | No | Active |
| POST | /api/v1/chat/stream | Streaming | Yes | Yes | Active |
| POST | /api/v1/chat/welcome | Chat | Yes | No | Active |
| POST | /api/v1/consultation-evaluation/batch | Consultation | Yes (staff) | No | Active |
| POST | /api/v1/consultation-evaluation/manual | Consultation | Yes | No | Active |
| POST | /api/v1/consultation-evaluation/manual-override | Consultation | Yes (staff) | No | Active |
| GET | /api/v1/consultation-evaluation/status | Consultation | Yes | No | Active |
| GET | /api/v1/habits | Tracking | Yes | No | Active |
| POST | /api/v1/habits | Tracking | Yes | No | Active |
| POST | /api/v1/habits/streaks | Tracking | Yes | No | Active |
| PATCH | /api/v1/habits/{habit_id} | Tracking | Yes | No | Active |
| POST | /api/v1/habits/{habit_id}/check-in | Tracking | Yes | No | Active |
| GET | /api/v1/health | Health | No | No | Active |
| GET | /api/v1/health/live | Health | No | No | Active |
| GET | /api/v1/health/ready | Health | No | No | Active |
| GET | /api/v1/journal/calendar-data | Journaling | Yes | No | Active |
| POST | /api/v1/journal/entry | Journaling | Yes | No | Active |
| GET | /api/v1/journal/entry/{entry_id} | Journaling | Yes | No | Active |
| GET | /api/v1/journal/favorites | Journaling | Yes | No | Active |
| GET | /api/v1/journal/monthly-mindfulness | Journaling | Yes | No | Active |
| GET | /api/v1/journal/past-reflections | Journaling | Yes | No | Active |
| GET | /api/v1/journal/recent-entries | Journaling | Yes | No | Active |
| GET | /api/v1/journal/stats | Journaling | Yes | No | Active |
| POST | /api/v1/language | Language | Yes | No | Active |
| POST | /api/v1/meditation/complete | Meditation | Yes | No | Active |
| POST | /api/v1/meditation/feedback | Meditation | Yes | No | Active |
| POST | /api/v1/meditation/preview | Meditation | Yes | No | Active |
| POST | /api/v1/meditation/start | Meditation | Yes | No | Active |
| DELETE | /api/v1/memory | Adaptive Memory | Yes | No | Active |
| POST | /api/v1/memory/consent | Adaptive Memory | Yes | No | Active |
| POST | /api/v1/memory/consolidate | Adaptive Memory | Yes | No | Active |
| GET | /api/v1/memory/profile | Adaptive Memory | Yes | No | Active |
| POST | /api/v1/memory/feedback | Adaptive Memory | Yes | No | Active |
| POST | /api/v1/mood | Tracking | Yes | No | Active |
| GET | /api/v1/mood/recent | Tracking | Yes | No | Active |
| POST | /api/v1/patterns/feedback | Pattern Detection | Yes | No | Active |
| POST | /api/v1/report_card/tasks/complete | Tasks | Yes | No | Active |
| POST | /api/v1/report_card/tasks/custom | Tasks | Yes | No | Active |
| PATCH | /api/v1/report_card/tasks/custom/{claimed_user_id}/{task_id} | Tasks | Yes | No | Active |
| GET | /api/v1/report_card/tasks/{claimed_user_id} | Tasks | Yes | No | Active |
| POST | /api/v1/reports/tasks/accept | Reports | Yes | No | Active |
| POST | /api/v1/session/report | Reports | Yes | No | Active |
| POST | /api/v1/sleep | Sleep | Yes | No | Active |
| GET | /api/v1/sleep/history | Sleep | Yes | No | Active |
| GET | /api/v1/sleep/recent | Sleep | Yes | No | Active |
| POST | /api/v1/voice/stt | Voice | Yes | No | Active |
| WS | /api/v1/ws/psychiatrist-voice | Voice | Yes | Yes | Active |
| GET | /api/habits | Tracking | Yes | No | Compatibility |
| POST | /api/habits | Tracking | Yes | No | Compatibility |
| POST | /api/habits/streaks | Tracking | Yes | No | Compatibility |
| PATCH | /api/habits/{habit_id} | Tracking | Yes | No | Compatibility |
| POST | /api/habits/{habit_id}/check-in | Tracking | Yes | No | Compatibility |
| POST | /api/language | Language | Yes | No | Compatibility |
| POST | /api/meditation/complete | Meditation | Yes | No | Compatibility |
| POST | /api/meditation/feedback | Meditation | Yes | No | Compatibility |
| POST | /api/meditation/preview | Meditation | Yes | No | Compatibility |
| POST | /api/meditation/start | Meditation | Yes | No | Compatibility |
| DELETE | /api/memory | Adaptive Memory | Yes | No | Compatibility |
| POST | /api/memory/consent | Adaptive Memory | Yes | No | Compatibility |
| POST | /api/memory/consolidate | Adaptive Memory | Yes | No | Compatibility |
| GET | /api/memory/profile | Adaptive Memory | Yes | No | Compatibility |
| POST | /api/memory/feedback | Adaptive Memory | Yes | No | Compatibility |
| POST | /api/mood | Tracking | Yes | No | Compatibility |
| GET | /api/mood/recent | Tracking | Yes | No | Compatibility |
| POST | /api/patterns/feedback | Pattern Detection | Yes | No | Compatibility |
| POST | /api/report_card/tasks/complete | Tasks | Yes | No | Compatibility |
| POST | /api/report_card/tasks/custom | Tasks | Yes | No | Compatibility |
| PATCH | /api/report_card/tasks/custom/{claimed_user_id}/{task_id} | Tasks | Yes | No | Compatibility |
| GET | /api/report_card/tasks/{claimed_user_id} | Tasks | Yes | No | Compatibility |
| POST | /api/reports/tasks/accept | Reports | Yes | No | Compatibility |
| POST | /api/session/report | Reports | Yes | No | Compatibility |
| POST | /api/sleep | Sleep | Yes | No | Compatibility |
| GET | /api/sleep/history | Sleep | Yes | No | Compatibility |
| GET | /api/sleep/recent | Sleep | Yes | No | Compatibility |
| POST | /auth/login | Authentication | No | No | Compatibility |
| POST | /auth/signup | Authentication | No | No | Compatibility |
| POST | /chat/send | Chat | Yes | No | Compatibility |
| GET | /chat/session/{user_id}/resume | Chat | Yes | No | Compatibility |
| POST | /chat/stream | Streaming | Yes | Yes | Compatibility |
| POST | /chat/welcome | Chat | Yes | No | Compatibility |
| POST | /consultation-evaluation/batch | Consultation | Yes (staff) | No | Compatibility |
| POST | /consultation-evaluation/manual | Consultation | Yes | No | Compatibility |
| POST | /consultation-evaluation/manual-override | Consultation | Yes (staff) | No | Compatibility |
| GET | /consultation-evaluation/status | Consultation | Yes | No | Compatibility |
| GET | /health | Health | No | No | Compatibility |
| GET | /health/live | Health | No | No | Compatibility |
| GET | /health/ready | Health | No | No | Compatibility |
| GET | /journal/calendar-data | Journaling | Yes | No | Compatibility |
| POST | /journal/entry | Journaling | Yes | No | Compatibility |
| GET | /journal/entry/{entry_id} | Journaling | Yes | No | Compatibility |
| GET | /journal/favorites | Journaling | Yes | No | Compatibility |
| GET | /journal/monthly-mindfulness | Journaling | Yes | No | Compatibility |
| GET | /journal/past-reflections | Journaling | Yes | No | Compatibility |
| GET | /journal/recent-entries | Journaling | Yes | No | Compatibility |
| GET | /journal/stats | Journaling | Yes | No | Compatibility |
| POST | /voice/stt | Voice | Yes | No | Compatibility |
| WS | /ws/psychiatrist-voice | Voice | Yes | Yes | Compatibility |

## School dashboard

Authenticated principals, school admins, admins, and counselors. The school is taken from the account, never from the query string. Success bodies use `{success, data, meta}`. Full contract: `docs/dashboard-api.md`.

| Method | Endpoint | Feature | Auth | Streaming | Status |
|---|---|---|---|---|---|
| GET | /api/v1/dashboard/context | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/academic-years | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/grades | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/classes | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/subjects | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/overview | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/classes/{class_id}/overview | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/classes/{class_id}/performance-quadrant | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/classes/{class_id}/students | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/classes/{class_id}/subjects/{subject_id}/overview | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/students/{student_id}/profile | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/students/{student_id}/interventions | School dashboard | Yes (school staff) | No | Active |
| POST | /api/v1/dashboard/students/{student_id}/parent-contact | School dashboard | Yes (school staff) | No | Active |
| POST | /api/v1/dashboard/students/{student_id}/notify-counselor | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/risk/summary | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/risk/students | School dashboard | Yes (school staff) | No | Active |
| POST | /api/v1/dashboard/interventions | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/interventions/{intervention_id} | School dashboard | Yes (school staff) | No | Active |
| PATCH | /api/v1/dashboard/interventions/{intervention_id} | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/teachers | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/teachers/{teacher_id} | School dashboard | Yes (school staff) | No | Active |
| POST | /api/v1/dashboard/teachers/{teacher_id}/messages | School dashboard | Yes (school staff) | No | Active |
| POST | /api/v1/dashboard/teachers/{teacher_id}/reviews | School dashboard | Yes (school staff) | No | Active |
| POST | /api/v1/dashboard/teachers/{teacher_id}/support-plan | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/analytics/grade-trends | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/analytics/subject-performance | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/analytics/wellbeing | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/analytics/comparative | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/notifications | School dashboard | Yes (school staff) | No | Active |
| POST | /api/v1/dashboard/notifications/{notification_id}/read | School dashboard | Yes (school staff) | No | Active |
| POST | /api/v1/dashboard/notifications/read-all | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/events | School dashboard | Yes (school staff) | Yes | Active |
| POST | /api/v1/dashboard/reports | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/reports | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/reports/{report_id} | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/reports/{report_id}/preview | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/reports/{report_id}/download | School dashboard | Yes (school staff) | No | Active |
| POST | /api/v1/dashboard/assistant | School dashboard | Yes (school staff) | No | Active |
| GET | /api/v1/dashboard/settings | School dashboard | Yes (school staff) | No | Active |
| PATCH | /api/v1/dashboard/settings | School dashboard | Yes (school staff) | No | Active |

