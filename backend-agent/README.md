# Zenark backend-agent

Mental-health API and Exam Buddy. This process owns chat, streaming, voice, safety, crisis handling, EOS, GDS, APM, the therapeutic graph, psychological memory, proactive questions, meditation, journaling, mood, sleep, habits, tasks, consultation, and language.

## backend-core dependency

Account, encryption, Mongo, settings, tenancy, mark reads, and Bedrock live in the `zenark-backend-core` package. This application imports those modules. It does not call backend-core over HTTP.

Inside the monolith:

```powershell
pip install -e ../backend-core
pip install -r requirements.txt
```

A standalone clone pins a commit of https://github.com/Zenark-2025/backend-core instead of the sibling path. Do not copy backend-core into this tree.

## Environment

Use a gitignored `.env` in the monolith root, or beside this app when it is cloned alone. Settings read `MONGODB_URI`, `DATABASE_NAME` or `MONGO_DB_NAME`, `ENCRYPTION_SECRET_KEY`, `AUTH_SIGNING_SECRET`, Bedrock credentials, and `SARVAM_API_KEY` for voice. Do not commit real values.

## Local startup

```powershell
python run.py
```

Entrypoint: `app.py` (`api.application.create_app`). Port 8000.

Historical routes stay on this process, including `POST /chat/send`, `POST /chat/stream`, voice, Exam Buddy, and `/auth/login`. `/api/v1/dashboard` is not mounted here.

## Docker

From the monolith root:

```powershell
docker build -f backend-agent/Dockerfile -t zenark-agent .
```

That image copies `backend-core` from the monolith context. A standalone clone installs `zenark-backend-core` from Git instead.

## Tests

From the monolith root:

```powershell
python -m pytest backend-agent/tests backend-agent/exam_buddy_guardrails/tests -q
```

## Security

Tokens are verified with the shared `backend_core` implementation. Payloads at rest use the shared encryption helpers. Do not log secrets. Staging refuses placeholder encryption and auth secrets.
