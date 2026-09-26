# What "encrypted" actually means here

The product specification (section 12) says user content is end-to-end
encrypted. That is not what this system does, and it cannot be while the rest
of the specification stands. This note records the real design so the claim can
be corrected rather than quietly believed.

## What is implemented

Server-side encryption at rest, in `services/security.py`:

- `encrypt_payload()` / `decrypt_payload()` use Fernet (AES-128-CBC with an
  HMAC), keyed by PBKDF2-HMAC-SHA256 over `ENCRYPTION_SECRET_KEY` with a fixed
  salt. Stored values carry an `enc::` prefix so unencrypted legacy rows stay
  readable.
- `anonymize_text()` redacts Indian mobile numbers, email addresses, and
  Aadhaar numbers before text is sent to an external model, when
  `ENFORCE_PII_ANONYMIZATION` is on.

Applied to chat message content in `services/chat_history.py`,
`services/graph.py`, `services/streaming.py`, and `services/session_resume.py`.

Stored as plaintext today: journal entries, sleep logs, mood check-ins, habits,
tasks, session reports, student memory facts, detected patterns, and
consultation notifications.

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

So the honest boundary is: content is unreadable to someone who steals the
database alone, and readable to this server, to anyone holding
`ENCRYPTION_SECRET_KEY`, and to the model provider for the text of a turn.

## Known weaknesses of the current scheme

1. **One key for all users.** A single `ENCRYPTION_SECRET_KEY` protects
   everyone's messages. There is no per-user key and no key rotation path;
   rotating means re-encrypting every message.
2. **Static salt.** Deliberate, so the same secret yields the same key, but it
   means the derivation adds no per-deployment uniqueness beyond the secret.
3. **Failures degrade to plaintext.** If encryption raises, `encrypt_payload`
   logs and returns the original string so a write never fails. Data is kept at
   the cost of a silently unencrypted row.
4. **Coverage is partial.** Moods, journals, and reports are as sensitive as
   chat and are not encrypted at all.

## What the specification should say

> User content is encrypted in transit (TLS) and at rest. The service decrypts
> content server-side to generate responses, detect crisis signals, and compute
> insights, and sends message text to a model provider for that purpose. It is
> not end-to-end encrypted, and the operator can read user content.

## If true end-to-end is wanted

It is a product decision, not a library swap. Client-held keys would mean
losing server-side crisis detection, cross-session memory, insights, reports,
and the consultation pathway, or moving all of them onto the device. A
realistic middle path, in order of value:

1. Extend at-rest encryption to journals, moods, and session reports.
2. Move to per-user data keys wrapped by a master key in a KMS, which makes
   rotation and per-user deletion tractable.
3. Keep model calls out of scope for E2EE and say so plainly in the consent
   copy, rather than claiming a property the architecture does not have.
