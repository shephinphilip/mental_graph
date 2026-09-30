# API ownership

Historical routes are unchanged. backend-agent serves the mental-health and Exam Buddy API, including login. The dashboard serves `/api/v1/dashboard`. backend-core does not publish a product route. Its process exposes `/health/live` and `/health/ready` on its own port.

The public staging listener is the gateway on port 8000. It forwards `/api/v1/dashboard` to the dashboard process and every other path to backend-agent.

The published backend-agent repository serves the backend-agent rows. The published backend-core repository does not add product routes. Dashboard rows remain in this repository.

| Method | Route | Application | Auth | Notes |
|---|---|---|---|---|
| POST | /api/exam-buddy/ask | backend-agent | Bearer |  |
| GET | /api/habits | backend-agent | Bearer |  |
| POST | /api/habits | backend-agent | Bearer |  |
| POST | /api/habits/streaks | backend-agent | Bearer |  |
| PATCH | /api/habits/{habit_id} | backend-agent | Bearer |  |
| POST | /api/habits/{habit_id}/check-in | backend-agent | Bearer |  |
| POST | /api/language | backend-agent | Bearer |  |
| POST | /api/meditation/complete | backend-agent | Bearer |  |
| POST | /api/meditation/feedback | backend-agent | Bearer |  |
| POST | /api/meditation/preview | backend-agent | Bearer |  |
| POST | /api/meditation/start | backend-agent | Bearer |  |
| DELETE | /api/memory | backend-agent | Bearer |  |
| POST | /api/memory/consent | backend-agent | Bearer |  |
| POST | /api/memory/consolidate | backend-agent | Bearer |  |
| POST | /api/memory/erasure | backend-agent | Bearer |  |
| POST | /api/memory/feedback | backend-agent | Bearer |  |
| GET | /api/memory/profile | backend-agent | Bearer |  |
| POST | /api/mood | backend-agent | Bearer |  |
| GET | /api/mood/recent | backend-agent | Bearer |  |
| POST | /api/patterns/feedback | backend-agent | Bearer |  |
| POST | /api/proactive/evaluate | backend-agent | Bearer |  |
| GET | /api/proactive/pending | backend-agent | Bearer |  |
| POST | /api/proactive/respond | backend-agent | Bearer |  |
| POST | /api/report_card/tasks/complete | backend-agent | Bearer |  |
| POST | /api/report_card/tasks/custom | backend-agent | Bearer |  |
| PATCH | /api/report_card/tasks/custom/{claimed_user_id}/{task_id} | backend-agent | Bearer |  |
| GET | /api/report_card/tasks/{claimed_user_id} | backend-agent | Bearer |  |
| POST | /api/reports/tasks/accept | backend-agent | Bearer |  |
| POST | /api/session/report | backend-agent | Bearer |  |
| POST | /api/sleep | backend-agent | Bearer |  |
| GET | /api/sleep/history | backend-agent | Bearer |  |
| GET | /api/sleep/recent | backend-agent | Bearer |  |
| POST | /api/v1/auth/login | backend-agent | public |  |
| POST | /api/v1/auth/signup | backend-agent | public |  |
| POST | /api/v1/chat/send | backend-agent | Bearer |  |
| GET | /api/v1/chat/session/{user_id}/resume | backend-agent | Bearer |  |
| POST | /api/v1/chat/stream | backend-agent | Bearer |  |
| POST | /api/v1/chat/welcome | backend-agent | Bearer |  |
| POST | /api/v1/consultation-evaluation/batch | backend-agent | Bearer |  |
| POST | /api/v1/consultation-evaluation/manual | backend-agent | Bearer |  |
| POST | /api/v1/consultation-evaluation/manual-override | backend-agent | Bearer |  |
| GET | /api/v1/consultation-evaluation/status | backend-agent | Bearer |  |
| GET | /api/v1/dashboard/academic-years | dashboard | Bearer |  |
| GET | /api/v1/dashboard/analytics/comparative | dashboard | Bearer |  |
| GET | /api/v1/dashboard/analytics/grade-trends | dashboard | Bearer |  |
| GET | /api/v1/dashboard/analytics/subject-performance | dashboard | Bearer |  |
| GET | /api/v1/dashboard/analytics/wellbeing | dashboard | Bearer |  |
| POST | /api/v1/dashboard/assistant | dashboard | Bearer |  |
| GET | /api/v1/dashboard/classes | dashboard | Bearer |  |
| GET | /api/v1/dashboard/classes/{class_id}/overview | dashboard | Bearer |  |
| GET | /api/v1/dashboard/classes/{class_id}/performance-quadrant | dashboard | Bearer |  |
| GET | /api/v1/dashboard/classes/{class_id}/students | dashboard | Bearer |  |
| GET | /api/v1/dashboard/classes/{class_id}/subjects/{subject_id}/overview | dashboard | Bearer |  |
| GET | /api/v1/dashboard/context | dashboard | Bearer |  |
| GET | /api/v1/dashboard/events | dashboard | Bearer |  |
| GET | /api/v1/dashboard/grades | dashboard | Bearer |  |
| POST | /api/v1/dashboard/interventions | dashboard | Bearer |  |
| GET | /api/v1/dashboard/interventions/{intervention_id} | dashboard | Bearer |  |
| PATCH | /api/v1/dashboard/interventions/{intervention_id} | dashboard | Bearer |  |
| GET | /api/v1/dashboard/notifications | dashboard | Bearer |  |
| POST | /api/v1/dashboard/notifications/read-all | dashboard | Bearer |  |
| POST | /api/v1/dashboard/notifications/{notification_id}/read | dashboard | Bearer |  |
| GET | /api/v1/dashboard/overview | dashboard | Bearer |  |
| GET | /api/v1/dashboard/reports | dashboard | Bearer |  |
| POST | /api/v1/dashboard/reports | dashboard | Bearer |  |
| GET | /api/v1/dashboard/reports/{report_id} | dashboard | Bearer |  |
| GET | /api/v1/dashboard/reports/{report_id}/download | dashboard | Bearer |  |
| GET | /api/v1/dashboard/reports/{report_id}/preview | dashboard | Bearer |  |
| GET | /api/v1/dashboard/risk/students | dashboard | Bearer |  |
| GET | /api/v1/dashboard/risk/summary | dashboard | Bearer |  |
| GET | /api/v1/dashboard/settings | dashboard | Bearer |  |
| PATCH | /api/v1/dashboard/settings | dashboard | Bearer |  |
| GET | /api/v1/dashboard/students/{student_id}/interventions | dashboard | Bearer |  |
| POST | /api/v1/dashboard/students/{student_id}/notify-counselor | dashboard | Bearer |  |
| POST | /api/v1/dashboard/students/{student_id}/parent-contact | dashboard | Bearer |  |
| GET | /api/v1/dashboard/students/{student_id}/profile | dashboard | Bearer |  |
| GET | /api/v1/dashboard/subjects | dashboard | Bearer |  |
| GET | /api/v1/dashboard/teachers | dashboard | Bearer |  |
| GET | /api/v1/dashboard/teachers/{teacher_id} | dashboard | Bearer |  |
| POST | /api/v1/dashboard/teachers/{teacher_id}/messages | dashboard | Bearer |  |
| POST | /api/v1/dashboard/teachers/{teacher_id}/reviews | dashboard | Bearer |  |
| POST | /api/v1/dashboard/teachers/{teacher_id}/support-plan | dashboard | Bearer |  |
| POST | /api/v1/exam-buddy/ask | backend-agent | Bearer |  |
| GET | /api/v1/habits | backend-agent | Bearer |  |
| POST | /api/v1/habits | backend-agent | Bearer |  |
| POST | /api/v1/habits/streaks | backend-agent | Bearer |  |
| PATCH | /api/v1/habits/{habit_id} | backend-agent | Bearer |  |
| POST | /api/v1/habits/{habit_id}/check-in | backend-agent | Bearer |  |
| GET | /api/v1/health | backend-agent | Bearer |  |
| GET | /api/v1/health/live | backend-agent | Bearer |  |
| GET | /api/v1/health/ready | backend-agent | Bearer |  |
| GET | /api/v1/journal/calendar-data | backend-agent | Bearer |  |
| POST | /api/v1/journal/entry | backend-agent | Bearer |  |
| GET | /api/v1/journal/entry/{entry_id} | backend-agent | Bearer |  |
| GET | /api/v1/journal/favorites | backend-agent | Bearer |  |
| GET | /api/v1/journal/monthly-mindfulness | backend-agent | Bearer |  |
| GET | /api/v1/journal/past-reflections | backend-agent | Bearer |  |
| GET | /api/v1/journal/recent-entries | backend-agent | Bearer |  |
| GET | /api/v1/journal/stats | backend-agent | Bearer |  |
| POST | /api/v1/language | backend-agent | Bearer |  |
| POST | /api/v1/meditation/complete | backend-agent | Bearer |  |
| POST | /api/v1/meditation/feedback | backend-agent | Bearer |  |
| POST | /api/v1/meditation/preview | backend-agent | Bearer |  |
| POST | /api/v1/meditation/start | backend-agent | Bearer |  |
| DELETE | /api/v1/memory | backend-agent | Bearer |  |
| POST | /api/v1/memory/consent | backend-agent | Bearer |  |
| POST | /api/v1/memory/consolidate | backend-agent | Bearer |  |
| POST | /api/v1/memory/erasure | backend-agent | Bearer |  |
| POST | /api/v1/memory/feedback | backend-agent | Bearer |  |
| GET | /api/v1/memory/profile | backend-agent | Bearer |  |
| POST | /api/v1/mood | backend-agent | Bearer |  |
| GET | /api/v1/mood/recent | backend-agent | Bearer |  |
| POST | /api/v1/patterns/feedback | backend-agent | Bearer |  |
| POST | /api/v1/proactive/evaluate | backend-agent | Bearer |  |
| GET | /api/v1/proactive/pending | backend-agent | Bearer |  |
| POST | /api/v1/proactive/respond | backend-agent | Bearer |  |
| POST | /api/v1/report_card/tasks/complete | backend-agent | Bearer |  |
| POST | /api/v1/report_card/tasks/custom | backend-agent | Bearer |  |
| PATCH | /api/v1/report_card/tasks/custom/{claimed_user_id}/{task_id} | backend-agent | Bearer |  |
| GET | /api/v1/report_card/tasks/{claimed_user_id} | backend-agent | Bearer |  |
| POST | /api/v1/reports/tasks/accept | backend-agent | Bearer |  |
| POST | /api/v1/session/report | backend-agent | Bearer |  |
| POST | /api/v1/sleep | backend-agent | Bearer |  |
| GET | /api/v1/sleep/history | backend-agent | Bearer |  |
| GET | /api/v1/sleep/recent | backend-agent | Bearer |  |
| POST | /api/v1/voice/stt | backend-agent | Bearer |  |
| WS | /api/v1/ws/psychiatrist-voice | backend-agent | token query |  |
| POST | /auth/login | backend-agent | public |  |
| POST | /auth/signup | backend-agent | public |  |
| POST | /chat/send | backend-agent | Bearer |  |
| GET | /chat/session/{user_id}/resume | backend-agent | Bearer |  |
| POST | /chat/stream | backend-agent | Bearer |  |
| POST | /chat/welcome | backend-agent | Bearer |  |
| POST | /consultation-evaluation/batch | backend-agent | Bearer |  |
| POST | /consultation-evaluation/manual | backend-agent | Bearer |  |
| POST | /consultation-evaluation/manual-override | backend-agent | Bearer |  |
| GET | /consultation-evaluation/status | backend-agent | Bearer |  |
| GET | /docs | backend-agent | public | Also exposed on each process for its own health check. The public gateway sends this path to backend-agent. |
| GET | /docs/oauth2-redirect | backend-agent | public | Also exposed on each process for its own health check. The public gateway sends this path to backend-agent. |
| GET | /health | backend-agent | public | Also exposed on each process for its own health check. The public gateway sends this path to backend-agent. |
| GET | /health/live | backend-agent | public | Also exposed on each process for its own health check. The public gateway sends this path to backend-agent. |
| GET | /health/ready | backend-agent | public | Also exposed on each process for its own health check. The public gateway sends this path to backend-agent. |
| GET | /journal/calendar-data | backend-agent | Bearer |  |
| POST | /journal/entry | backend-agent | Bearer |  |
| GET | /journal/entry/{entry_id} | backend-agent | Bearer |  |
| GET | /journal/favorites | backend-agent | Bearer |  |
| GET | /journal/monthly-mindfulness | backend-agent | Bearer |  |
| GET | /journal/past-reflections | backend-agent | Bearer |  |
| GET | /journal/recent-entries | backend-agent | Bearer |  |
| GET | /journal/stats | backend-agent | Bearer |  |
| GET | /openapi.json | backend-agent | public | Also exposed on each process for its own health check. The public gateway sends this path to backend-agent. |
| GET | /redoc | backend-agent | public | Also exposed on each process for its own health check. The public gateway sends this path to backend-agent. |
| POST | /voice/stt | backend-agent | Bearer |  |
| WS | /ws/psychiatrist-voice | backend-agent | token query |  |
