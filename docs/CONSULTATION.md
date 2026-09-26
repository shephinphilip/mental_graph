# Psychiatric evaluation

`consultation/` decides whether recent conversations warrant professional care. It reads signals other modules already store. It does not score messages itself.

## Decision

One of three values per run:

- `REFERRED` — professional consultation is recommended; a notification is written.
- `MONITORING` — not referral-eligible; keep observing.
- `NOT_NEEDED` — no indication of need.

## Signals (last 14 days, `CONSULTATION_LOOKBACK_DAYS`)

Referral when any one holds:

- the crisis flag on the user was set inside the window
- a recent session report carried a crisis signal
- the `ESTABLISHED_PERSISTENT_DISTRESS` pattern from the risk window is active
- at least 2 of the last 3 session reports have `psychiatric_metric` ≥ 7
- at least 6 non-crisis turns with average risk ≥ `RISK_HIGH_THRESHOLD`

Monitoring when one report is elevated, or a raised risk average over at least 3 turns. Otherwise not needed. Weak signals do not add up to a referral.

Crisis turns are excluded from the average on purpose. The crisis fast-track handles them at the moment they happen.

## Triggers

- Automatic: after every session report, `evaluate_user_for_consultation()` runs. It never raises into the report.
- `POST /consultation-evaluation/manual` — the signed-in user evaluates themself. Staff may pass another `user_id`.
- `POST /consultation-evaluation/manual-override` — staff set the decision with a reason. Staff roles: `staff`, `admin`, `counselor`, `psychiatrist`.
- `POST /consultation-evaluation/batch` — staff evaluate a list of users.
- `GET /consultation-evaluation/status` — the signed-in user's latest status and unread notification count. Signals and audit rows are not returned.

## Cooldown and audit

A `REFERRED` decision writes one notification. A second referral within 30 days (`CONSULTATION_COOLDOWN_DAYS`) records `cooldown_active: true` and writes nothing new. Every run writes `EVALUATION_RUN` to the audit collection; referrals also write `NOTIFICATION_WRITTEN` or `REFERRAL_SUPPRESSED_COOLDOWN`; overrides write `MANUAL_OVERRIDE` with the reason.

Notifications are not consumed by a scheduling surface yet.

## Collections

| Collection | Purpose | Key fields |
|---|---|---|
| `psychiatric_evaluations` | results | `userId`, `evaluation_timestamp`, `status`, `care_recommendation`, `reasons`, `signals`, `trigger`, `cooldown_active`, `notification_written` |
| `consultation_notifications` | downstream | `userId`, `notification_type`, `read`, `payload`, `created_at` |
| `psychiatric_evaluation_audit` | audit | `userId`, `action`, `timestamp`, `actor`, `details` |

## Chat

The latest status becomes `{care_context}` in the system prompt. For `REFERRED` the model is told to support care warmly if the person raises it, and not to push or repeat it. Monitoring and not-needed are not mentioned. The psychiatrist action card is still attached only by the per-turn risk window.
