# What "encrypted" actually means here

Canonical current-state inventory: [`ENCRYPTION_DATA_MATRIX.md`](ENCRYPTION_DATA_MATRIX.md).

User content is encrypted in transit and at rest. The service decrypts content server-side when required for safety processing, conversational context, memory, insights, reporting, and approved model-provider calls. This is not end-to-end encryption.

The product specification (section 12) asked for end-to-end encryption. That is not what this system does, and it cannot be while crisis screening, chat context, memory, reports, and model calls remain server-side.

## What is implemented

Server-side encryption at rest, in `services/security.py`:

- `encrypt_payload()` / `decrypt_payload()` use Fernet (AES-128-CBC with an
  HMAC), keyed by PBKDF2-HMAC-SHA256 over `ENCRYPTION_SECRET_KEY` with a fixed
  salt. Stored values carry an `enc::` prefix so unencrypted legacy rows stay
  readable.
- Encrypt failures raise `CryptoIntegrityError` and must not persist plaintext.
- `enc::` values that cannot be opened with the current key raise
  `CryptoIntegrityError`. Ciphertext is never returned to API clients.
- `anonymize_text()` redacts Indian mobile numbers, email addresses, and
  Aadhaar numbers before text is sent to an external model, when
  `ENFORCE_PII_ANONYMIZATION` is on.

Sealed at rest today (`collection.field`):

- `messages.content`
- `journal_entries.content`
- `mood_logs.note`
- `student_memories.fact`
- `session_reports.summary`
- `session_reports.psychiatric_summary`
- `user_insights.insight_summary`
- `graph_nodes.name`

See the matrix for plaintext narrative fields (journal titles, task text, pattern descriptions, sanitized graph relationship properties, derived-safe profile labels, and others). Those are documented gaps, not E2EE. `users.memory_summary` is not a current writer.

## Why it is not end-to-end

End-to-end encryption means the server holds ciphertext it cannot open. This
server must read content in the clear to do the things the same specification
asks for:

- **Section 2** — the chatbot answers with context, so the message and the
  history must be readable at request time, and are then sent to a model
  provider (Bedrock, with Sarvam as fallback).
- **Section 3** — insights, correlations, and pattern detection are computed
  server-side over moods, sleep, marks, and journals.
- **Section 8** — crisis detection runs on message text before a reply is
  generated. An unreadable message cannot be screened.

So the honest boundary is: sealed fields are unreadable to someone who steals
the database alone, and readable to this server, to anyone holding
`ENCRYPTION_SECRET_KEY`, and to the model provider for opened text of a turn.

## Known limitations of the current scheme

1. **One key for all users.** A single `ENCRYPTION_SECRET_KEY` protects
   everyone's sealed fields. There is no per-user key and no in-app key
   versioning; rotating means an offline re-encryption of every `enc::` row.
2. **Static salt.** Deliberate, so the same secret yields the same key, but it
   means the derivation adds no per-deployment uniqueness beyond the secret.
3. **Coverage is partial.** Titles, task text, pattern descriptions, and several derived stores remain plaintext. Memory facts are no longer copied onto `users`. The matrix lists remaining gaps.
4. **Model provider sees plaintext.** At-rest encryption is opened before the
   LLM call. Optional regex PII redaction is not E2EE.

## If true end-to-end is wanted

It is a product decision, not a library swap. Client-held keys would mean
losing server-side crisis detection, cross-session memory, insights, reports,
and the consultation pathway, or moving all of them onto the device. A
realistic middle path, in order of value:

1. Extend at-rest sealing to remaining free-text fields listed in the matrix
   (titles, tasks, pattern descriptions, derived copies).
2. Move to per-user data keys wrapped by a master key in a KMS, which makes
   rotation and per-user deletion tractable.
3. Keep model calls out of scope for E2EE and say so plainly in the consent
   copy, rather than claiming a property the architecture does not have.
