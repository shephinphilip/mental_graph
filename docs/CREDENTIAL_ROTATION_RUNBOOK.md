# Zenark credential rotation runbook

No secret values belong in this document. Treat every credential that ever appeared in Git history as compromised until an operator revokes it and confirms it is dead.

**Safe order:** rotate and revoke → verify old credentials fail → only then consider Git history purge.

Cursor / automation cannot complete vendor rotations without console access. Execution on 2026-09-29: **ROTATION PENDING — OPERATOR ACTION REQUIRED**. See §14.

---

## 1. Incident context

Pre-production audit (Round 1–2) found historical credentials in Git.

Current working tree is cleaned (empty Sarvam default, empty AWS defaults, non-credentialed local Mongo URI, `.env` not tracked). **A clean tree does not revoke leaked credentials.** Anyone with clone access to those commits can still try the old values.

This runbook is credential hygiene only. It does not change product behavior, GDS, safety policy, or architecture.

---

## 2. Exposed credential categories

Classified from Git objects **without printing values**. `ROTATE` means the stored value was not an empty/placeholder shape.

| Credential type | Variable / file | Current or historical | Commit(s) | Required action |
|-----------------|-----------------|----------------------|-----------|-----------------|
| Mongo URI with credentials | `MONGODB_URI` / `.env` | Historical | `10711e8` | **ROTATE** Mongo user password; never commit a credentialed URI |
| Third-party LLM key | `GEMINI_API_KEY` / `.env` | Historical | `10711e8` | **ROTATE** / revoke at Google AI |
| Third-party LLM key | `OPENAI_API_KEY` / `.env` | Historical | `10711e8` | **ROTATE** / revoke at OpenAI |
| Third-party GPU/API key | `NVIDIA_API_KEY` / `.env` | Historical | `10711e8` | **ROTATE** / revoke at NVIDIA |
| Graph DB URI | `NEO4J_URI` / `.env` | Historical | `10711e8` | **ROTATE** if Neo4j still exists; product no longer uses Neo4j |
| Graph DB password | `NEO4J_PASSWORD` / `.env` | Historical | `10711e8` | **ROTATE** / disable that user |
| Sarvam API key | `SARVAM_API_KEY` / `config/config.py` | Historical default | `c067dee4` | **ROTATE** — live-looking `api-key-shape` default. Current default is empty |
| AWS access key id | `AWS_ACCESS_KEY_ID` | Historical tracked files were **placeholder-or-empty** | `c067dee4`, `7253876`, `08530466` | **VERIFY in IAM** — no populated IAM key shape found in classified blobs; still inventory IAM in case keys were used locally or in untracked files |
| AWS secret | `AWS_SECRET_ACCESS_KEY` | Same | same | **VERIFY in IAM** |
| AWS session token | `AWS_SESSION_TOKEN` | Not found in classified blobs | — | Confirm unused |
| Encryption secret | `ENCRYPTION_SECRET_KEY` | Not in `10711e8` `.env` blob; public **placeholder** in current defaults / `.env.example` | `c067dee4` and later | Staging: new unique secret + wipe/reseed. Production: **do not swap blindly** — follow §7 |
| JWT signing secret | `AUTH_SIGNING_SECRET` | Not in `10711e8` `.env` blob; public **placeholder** in current defaults | `c067dee4`, `7253876` | Unique staging and production secrets. Production rotation invalidates tokens — follow §8 |
| `.env` file | `.env` | Historical; **not currently tracked** | Added `10711e8`; not present at `7253876` tree (removed/touched) | Rotate everything that was in that blob; keep untracked |

`.env.example` in those commits: placeholders only (`YOUR_AWS_*`, empty Sarvam). False positive: numeric Mongo pool settings are not secrets.

---

## 3. Current repository state

Verified in current source (defaults in `config/config.py`):

- `SARVAM_API_KEY` default `""`
- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` default `""`
- `MONGODB_URI` default `mongodb://localhost:27017` (no userinfo)
- `ENCRYPTION_SECRET_KEY` default is the **known development placeholder**, not a unique live key
- `AUTH_SIGNING_SECRET` default is the **known development placeholder**
- `git ls-files -- .env` empty
- `git ls-files .venv` empty after index cleanup
- No CI workflows in-repo
- Dockerfile copies the app; `.dockerignore` excludes `.env`
- Tests use dummy strings only (`super-secret-sarvam-key`, `SecurePass1`)
- Sarvam client redacts the key from provider error snippets (`integrations/sarvam.py`)

Development may keep using `.env` locally. Hardened `APP_ENV` still refuses placeholder encryption and auth (`core/runtime_guard.py`) and staging still refuses database name `mental_health`.

---

## 4. AWS rotation

No populated `AKIA…` shape was found in classified Git blobs. IAM access keys remain **ROTATION REQUIRED — verify in AWS IAM** because operators may have used long-lived keys that never landed in Git, or keys that lived only in local `.env`.

Operator checklist:

1. Inventory IAM identities used by Zenark (users, roles, access keys).
2. Identify any access keys that may correspond to historical repository or laptop credentials.
3. Deactivate/revoke affected credentials.
4. Create new **staging** credentials using the intended staging mechanism (IAM user or role; prefer instance/task role over static keys).
5. Create **separate production** credentials. Do not share with staging.
6. Do not reuse compromised credentials.
7. Store new credentials only in environment / secret management.
8. Never commit them.

Confirm old keys: AWS API calls with the old access key id are denied.

---

## 5. Sarvam rotation

Historical `SARVAM_API_KEY` default in `config/config.py` at `c067dee4` classified as `api-key-shape`. Current default is empty.

Operator checklist:

1. Revoke/disable the historical Sarvam credential in the Sarvam console.
2. Create a **new** staging Sarvam credential.
3. Create a **new** production Sarvam credential.
4. Store them only in staging/production environment or secret management as `SARVAM_API_KEY`.
5. Never place the new values in Git.
6. Confirm current source default remains empty (automated test).

Verify old key: STT/TTS with the old key returns unauthorized. Verify new staging key: a single controlled STT call succeeds.

---

## 6. Mongo rotation

Historical `.env` at `10711e8` contained a **credentialed Mongo URI**.

Operator checklist:

1. Identify the MongoDB user in that URI (do not paste the URI).
2. Rotate its password (or disable the user).
3. Create separate staging credentials where appropriate.
4. Staging uses an explicit `DATABASE_NAME` other than `mental_health` (`APP_ENV=staging` or `preprod`).
5. Production uses its intended database; do not point staging at it.
6. Update only secret storage / environment (`MONGODB_URI` / `MONGO_URI`).
7. Never commit credential-bearing URIs.

Current repo default remains `mongodb://localhost:27017`.

---

## 7. Encryption key rotation

**Implementation (fail-closed):**

- Algorithm: Fernet (AES-128-CBC + HMAC) via `cryptography`
- Key: PBKDF2-HMAC-SHA256, 100_000 iterations, 32-byte key, **static salt** `mental_health_salt_2026`
- Ciphertext marker: prefix `enc::`
- **No key versioning**
- Protected writers encrypt **before** the Mongo write. If encryption fails they raise `CryptoIntegrityError` (`Encryption failed`) and **do not persist plaintext, ciphertext fragments, or a mixed document**.
- `enc::` values that cannot be opened with the current key raise `CryptoIntegrityError` (`Decryption failed`). The API maps that to the existing 500 envelope (`INTERNAL_ERROR` / “An unexpected error occurred.”). Ciphertext is never returned to the client.
- Crypto logs record only `type=<ExceptionClass>` (for example `type=InvalidToken`). They must not contain plaintext or `enc::` payloads.
- Legacy rows **without** the `enc::` prefix still pass through unchanged (pre-encryption data and empty strings).
- Canonical field list: [`ENCRYPTION_DATA_MATRIX.md`](ENCRYPTION_DATA_MATRIX.md). Sealed today: `messages.content`, `journal_entries.content`, `mood_logs.note`, `student_memories.fact`, `session_reports.summary`, `session_reports.psychiatric_summary`, `user_insights.insight_summary`, `graph_nodes.name`.

Rotating `ENCRYPTION_SECRET_KEY` without a dual-key migrator makes existing `enc::` rows **unreadable**. Reads fail closed (structured 500), they do **not** echo ciphertext.

**Staging:** generate a new unique `ENCRYPTION_SECRET_KEY`. Wipe or reseed disposable staging data. Do not copy production ciphertext into staging.

**Production — do not rotate blindly.** Required sequence:

1. Establish a new key in a secret manager (not Git). Keep the **old** key available to a one-off migrator.
2. Preserve the old key until migration verifies.
3. For each sealed field (`enc::…`): decrypt with the old key; skip already-plaintext rows.
4. Re-encrypt with the new key; write back.
5. Verify sample reads (messages, journals, mood notes, memory facts, session reports, graph names, insights).
6. Point the API at the new key only after verification; then retire the old key from the secret manager.

Do not run a destructive production re-encryption from this runbook automatically. There is no dual-key reader in application code today; the migrator must hold both keys offline.

Also rotate any **Gemini / OpenAI / NVIDIA / Neo4j** secrets from `10711e8` even if unused by current Bedrock-only runtime.

---

## 8. JWT secret rotation

**Implementation:** HMAC-SHA256 over a base64 payload (`services/users.py`). Claims: `sub`, `exp`. TTL: `AUTH_TOKEN_TTL_SECONDS` default **43200** (12 hours). One signing secret. No key id, no previous-secret fallback.

Changing `AUTH_SIGNING_SECRET` makes every outstanding token fail `verify_access_token` immediately (forced logout). Tokens do not refresh themselves.

**Staging:** set a unique staging secret. Accept re-login.

**Production:** do not reuse historical or staging secrets. Options:

- **Maintenance window:** deploy new secret; all clients log in again.
- **Later engineering (not implemented here):** accept `AUTH_SIGNING_SECRET_PREVIOUS` for one TTL window, then remove it.

Never put the new secret in Git.

---

## 9. Secret-store requirements

Loading today: `pydantic-settings` + `load_dotenv` on the repo `.env` with **process environment taking precedence** (`override=False`).

| Plane | Mechanism |
|-------|-----------|
| LOCAL | `.env` (gitignored) with development placeholders allowed |
| STAGING | Inject env vars from the host secret manager / orchestrator. `APP_ENV=staging`. Explicit `DATABASE_NAME` ≠ `mental_health`. Unique encryption, JWT, AWS, Sarvam, Mongo |
| PRODUCTION | Same injection path. `APP_ENV=production`. Unique secrets. Never share staging credentials |

Do not commit filled `.env`. `.env.example` stays placeholders only.

Staging and production must not share encryption, JWT, AWS, Sarvam, or Mongo credentials.

---

## 10. Git-history purge procedure

**Do not purge before old credentials are confirmed dead.**

Not executed in this task. After rotation verification:

Recommended tool: [`git filter-repo`](https://github.com/newren/git-filter-repo) (not `git filter-branch`).

Illustrative commands (paths only, no secrets):

```text
# Backup first
git clone --mirror <origin-url> zenark-mirror-backup.git

# In a fresh clone
git filter-repo --invert-paths --path .env

# If a specific blob path also held keys, add --path-glob for that path
# after legal/ops review.
```

Effects:

- Rewrites commits that contained `.env`
- Requires **force push** to every remote
- All collaborators must **re-clone or reset** — old clones still have the leaked blobs
- Open PRs may need recreation

Coordinate with every fork. Force-pushing `main` without that coordination is destructive.

Optional: after force-push, contact GitHub/GitLab support to expire cached blobs.

---

## 11. Verification checklist

Use placeholders, never real secrets, in shells.

**Old credentials must fail**

- AWS: `aws sts get-caller-identity` with the old key id → `InvalidClientTokenId` / `AccessDenied`
- Sarvam: STT with old key → unauthorized
- Mongo: connect with old URI → auth failure
- Encryption: after production migration only, old key no longer used by the API
- JWT: after production cutover, tokens signed with the old secret are rejected

**New staging must work**

```text
APP_ENV=staging
DATABASE_NAME=<STAGING_DB_NOT_mental_health>
ENCRYPTION_SECRET_KEY=<NEW_STAGING_ENCRYPTION_SECRET>
AUTH_SIGNING_SECRET=<NEW_STAGING_AUTH_SECRET>
MONGODB_URI=<NEW_STAGING_MONGO_URI>
AWS_ACCESS_KEY_ID=<NEW_STAGING_AWS_KEY_ID>
AWS_SECRET_ACCESS_KEY=<NEW_STAGING_AWS_SECRET>
SARVAM_API_KEY=<NEW_STAGING_SARVAM_KEY>
```

Then: process starts; `/health/ready` is 200; one login; one chat or STT as applicable.

**New production must work** (after migration window): same pattern with production names and **different** values.

**Repo**

```text
git ls-files -- .env
git ls-files .venv
python -m pytest tests/security tests/test_mvp_contract.py tests/test_security.py -q
```

---

## 12. Rollback considerations

- **AWS / Sarvam / Mongo:** keep a break-glass path in the secret manager; do not reactivate leaked keys.
- **Encryption:** keep the old production key in the secret manager until the migrator and API both use the new key and a sample of `enc::` rows decrypt. Rolling back the API to the old key restores old ciphertext; mixed keys in the database are the failure mode to avoid.
- **JWT:** rolling back to the previous production signing secret restores tokens issued with that secret that have not expired; tokens issued with the new secret then fail.
- **Git purge:** irreversible without the mirror backup. Do not purge until keys are dead.

---

## 13. Final operator sign-off

Sign only after evidence, not because the source tree is clean.

| Check | Owner | Date | Evidence |
|-------|-------|------|----------|
| Old AWS keys inactive | operator | 2026-09-29 | **NOT COMPLETED.** Classified Git blobs had no `AKIA` shape. Local gitignored `.env` has an operator IAM user key (suffix `S4FB`). STS succeeded; `iam:ListAccessKeys` AccessDenied. No key deactivated. **AWS ROTATION REQUIRES OPERATOR IDENTIFICATION** |
| Old Sarvam key revoked | operator | 2026-09-29 | **NOT VERIFIED.** Historical default at `c067dee4` still produced HTTP 400 (not 401) on a controlled STT probe — treat as possibly still authorized. Current source default remains `""` |
| Old Mongo user rotated | operator | 2026-09-29 | Historical host `cluster0.30zvh8x.mongodb.net` user `zenark`: **DNS NXDOMAIN** (cluster hostname does not exist). Password rotation at Atlas **NOT VERIFIED**. Do not reuse that password |
| Gemini / OpenAI / NVIDIA / Neo4j from `10711e8` revoked | operator | 2026-09-29 | Gemini HTTP 403 **NOT VERIFIED**. OpenAI HTTP 401 (unauthorized). **NVIDIA HTTP 200 — still authorized, revoke immediately.** Neo4j unused in runtime; bolt probe **NOT COMPLETED** |
| Staging secrets unique and working | operator | 2026-09-29 | **NOT COMPLETED.** Placeholder contract documented. Docker daemon down. No isolated staging DB started. `/health/ready` not live-tested |
| Production encryption migration complete (if rotating prod) | operator | 2026-09-29 | **NOT DONE (correct).** `PRODUCTION ENCRYPTION ROTATION = REQUIRES DUAL-KEY MIGRATION` |
| Production JWT cutover communicated | operator | 2026-09-29 | **NOT DONE.** `PRODUCTION JWT ROTATION = OPERATOR CUTOVER REQUIRED` |
| Git history purge (optional, after keys dead) | operator | 2026-09-29 | **NOT EXECUTED.** NVIDIA credential still live. Do not rewrite history yet |
| `.env` still untracked | automation | 2026-09-29 | **COMPLETED.** `git ls-files -- .env` empty; `git ls-files .venv` empty |

Until NVIDIA (and remaining vendor keys) are confirmed dead: **ROTATION PENDING — OPERATOR ACTION REQUIRED**.

---

## 14. Execution log (2026-09-29)

This is operational evidence, not a design change. Secret values are not recorded here.

### COMPLETED

- Current tree: `SARVAM_API_KEY` default empty; AWS defaults empty; `MONGODB_URI` default `mongodb://localhost:27017`; encryption/JWT defaults are development placeholders; `.env` / `.venv` untracked.
- Tracked-file secret-shape scan: no `AKIA`, no credentialed Mongo URI, no live `sk-` assignments.
- Runtime classification: Bedrock + Sarvam speech are **ACTIVE**. Gemini, OpenAI, NVIDIA, Neo4j are **LEGACY / UNUSED** in application runtime (Neo4j remains only in a one-off migration script).
- Historical Mongo host `cluster0.30zvh8x.mongodb.net` no longer resolves (NXDOMAIN).
- Staging placeholder contract added to `.env.example` (comments only).
- Git-history purge **prepared** in §10 and **not executed**.

### NOT COMPLETED

- AWS IAM key rotation / deactivation
- New staging AWS credentials / role
- Sarvam historical key revocation
- New staging/production Sarvam keys in a secret manager
- Atlas password rotation confirmation for historical user `zenark`
- Isolated staging Mongo + `DATABASE_NAME` ≠ `mental_health`
- Docker image build / `/health/live` / `/health/ready`
- Staging JWT/encryption secret injection into a running process
- Gemini / NVIDIA / Neo4j console revocation
- Production encryption dual-key migration (intentionally not started)
- Production JWT cutover (intentionally not started)

### NOT VERIFIED

- Historical Sarvam key dead (STT probe returned HTTP 400, not 401)
- Historical Gemini key dead (HTTP 403)
- Local gitignored Mongo URI auth (TLS/OpenSSL `AttributeError` on this host)
- Staging login / token / Bedrock call

### REQUIRES OPERATOR

- Identify whether IAM user key suffix `S4FB` should remain; this agent will not deactivate an unmatched key (`iam:ListAccessKeys` denied)
- Revoke historical **NVIDIA** key (probe HTTP 200)
- Revoke/rotate Sarvam, Gemini, OpenAI, Neo4j at vendor consoles
- Create unique staging secrets in the secret manager (never Git)
- Start Docker Desktop and isolated staging Mongo
- After every exposed credential is confirmed dead: backup, inform collaborators, then consider `git filter-repo --invert-paths --path .env`

**Git purge authorization:** not granted. **Do not force-push.**
