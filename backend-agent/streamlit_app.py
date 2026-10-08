"""
Zenark companion — Streamlit client for every backend-agent HTTP API.

Run from the repository (with the API already up on port 8000):

    streamlit run backend-agent/streamlit_app.py

Or from backend-agent after putting backend-core on PYTHONPATH.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import requests
import streamlit as st

st.set_page_config(
    page_title="Zenark companion",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFAULT_API = "http://127.0.0.1:8000"
FALLBACK_WELCOME = (
    "I'm here. Whenever you're ready, tell me what's been sitting with you."
)
LANGUAGES = [
    "ENGLISH",
    "HINDI",
    "HINGLISH",
    "TELUGU",
    "TAMIL",
    "MALAYALAM",
    "KANNADA",
    "BENGALI",
    "GUJARATI",
    "PUNJABI",
    "ODIA",
    "URDU",
]
PAGES = [
    "Companion",
    "Mood & habits",
    "Sleep",
    "Journal",
    "Memory & proactive",
    "Exam Buddy",
    "Care & reports",
    "System",
]


# ── HTTP helpers ─────────────────────────────────────────────────────────────


def api_base() -> str:
    return st.session_state.get("api_url", DEFAULT_API).rstrip("/")


def auth_headers() -> dict:
    token = (st.session_state.get("user") or {}).get("access_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def api(
    method: str,
    path: str,
    *,
    json_body: Any = None,
    params: Optional[dict] = None,
    files: Any = None,
    timeout: int = 60,
    auth: bool = True,
) -> requests.Response:
    headers = dict(auth_headers()) if auth else {}
    if json_body is not None and files is None:
        headers["Content-Type"] = "application/json"
    return requests.request(
        method,
        f"{api_base()}{path}",
        headers=headers,
        json=json_body,
        params=params,
        files=files,
        timeout=timeout,
    )


def show_response(response: requests.Response) -> Any:
    try:
        payload = response.json()
    except Exception:
        payload = response.text
    if response.ok:
        st.success(f"{response.status_code}")
        if isinstance(payload, (dict, list)):
            st.json(payload)
        else:
            st.write(payload)
        return payload
    st.error(f"{response.status_code}")
    if isinstance(payload, (dict, list)):
        st.json(payload)
    else:
        st.code(str(payload)[:4000])
    return None


def user_id() -> str:
    return (st.session_state.get("user") or {}).get("user_id") or ""


def session_id() -> str:
    return st.session_state.get("session_id") or ""


# ── Session / chat core ──────────────────────────────────────────────────────


def fetch_welcome(uid: str, sid: str) -> dict:
    response = api(
        "POST",
        "/chat/send",
        json_body={"user_id": uid, "session_id": sid, "message": "WELCOME MESSAGE"},
        timeout=90,
    )
    response.raise_for_status()
    return response.json()


def start_new_session() -> None:
    user = st.session_state.user
    st.session_state.session_id = f"session_{user['user_id']}_{uuid.uuid4().hex[:10]}"
    st.session_state.messages = []
    st.session_state.pop("session_report", None)
    try:
        data = fetch_welcome(user["user_id"], st.session_state.session_id)
        reply = data.get("reply") or FALLBACK_WELCOME
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": reply,
                "action_cards": data.get("action_cards") or [],
            }
        )
        st.session_state.welcome_language = user.get("preferred_language") or "ENGLISH"
    except requests.exceptions.ConnectionError:
        st.session_state.welcome_error = "Cannot reach the API."
        st.session_state.messages.append(
            {"role": "assistant", "content": FALLBACK_WELCOME, "action_cards": []}
        )
    except Exception as exc:
        st.session_state.welcome_error = str(exc)
        st.session_state.messages.append(
            {"role": "assistant", "content": FALLBACK_WELCOME, "action_cards": []}
        )


def send_chat_text(message: str) -> dict:
    response = api(
        "POST",
        "/chat/send",
        json_body={
            "user_id": user_id(),
            "session_id": session_id(),
            "message": message,
        },
        timeout=90,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Error {response.status_code}: {response.text}")
    return response.json()


def stream_chat_text(message: str) -> str:
    """Consume SSE from /chat/stream and return the assembled reply."""
    response = api(
        "POST",
        "/chat/stream",
        json_body={
            "user_id": user_id(),
            "session_id": session_id(),
            "message": message,
        },
        timeout=120,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Error {response.status_code}: {response.text}")
    tokens: list[str] = []
    event_name = None
    for raw in response.text.splitlines():
        if raw.startswith("event:"):
            event_name = raw[6:].strip()
        elif raw.startswith("data:") and event_name == "token":
            try:
                payload = json.loads(raw[5:].strip())
            except json.JSONDecodeError:
                continue
            tokens.append(str(payload.get("token") or ""))
        elif raw.startswith("data:") and event_name == "error":
            try:
                payload = json.loads(raw[5:].strip())
            except json.JSONDecodeError:
                payload = {"error": raw[5:].strip()}
            raise RuntimeError(payload.get("error") or "stream error")
    return "".join(tokens)


def render_action_card(card: dict) -> None:
    title = card.get("title") or card.get("card_type") or "Action"
    subtitle = card.get("subtitle") or ""
    st.markdown(f"**{title}**")
    if subtitle:
        st.caption(subtitle)
    payload = card.get("action_payload") or {}
    card_type = card.get("card_type") or ""
    if card_type == "PATTERN_CARD" and payload.get("pattern_id"):
        c1, c2 = st.columns(2)
        if c1.button("Looks right", key=f"pat_yes_{payload['pattern_id']}"):
            api(
                "POST",
                "/api/patterns/feedback",
                json_body={
                    "pattern_id": payload["pattern_id"],
                    "event_type": "CONFIRMED",
                },
                timeout=10,
            )
        if c2.button("Not quite", key=f"pat_no_{payload['pattern_id']}"):
            api(
                "POST",
                "/api/patterns/feedback",
                json_body={
                    "pattern_id": payload["pattern_id"],
                    "event_type": "DISAGREED",
                },
                timeout=10,
            )
    if payload.get("edge_id") and payload.get("execution_nonce"):
        c1, c2 = st.columns(2)
        body = {
            "edge_id": payload["edge_id"],
            "intervention_id": payload.get("intervention_id") or "",
            "execution_nonce": payload["execution_nonce"],
        }
        if c1.button("This helped", key=f"mem_yes_{payload['execution_nonce']}"):
            api(
                "POST",
                "/api/memory/feedback",
                json_body={**body, "event_type": "HELPFUL"},
                timeout=10,
            )
        if c2.button("Not for me", key=f"mem_no_{payload['execution_nonce']}"):
            api(
                "POST",
                "/api/memory/feedback",
                json_body={**body, "event_type": "NOT_HELPFUL"},
                timeout=10,
            )


def append_chat(user_text: str, assistant: dict) -> None:
    st.session_state.messages.append({"role": "user", "content": user_text})
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": assistant.get("reply") or "",
            "action_cards": assistant.get("action_cards") or [],
        }
    )


# ── Auth gate ────────────────────────────────────────────────────────────────


def page_login() -> None:
    st.subheader("Sign in")
    st.caption("Demo password for seeded accounts: `Zenark@123`")
    tab_login, tab_signup = st.tabs(["Login", "Signup"])
    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email", value="meera.iyer@zenark.demo")
            password = st.text_input("Password", type="password", value="Zenark@123")
            submitted = st.form_submit_button("Enter", use_container_width=True)
        if submitted:
            try:
                resp = api(
                    "POST",
                    "/auth/login",
                    json_body={"email": email.strip(), "password": password},
                    timeout=15,
                    auth=False,
                )
                if resp.status_code == 401:
                    st.error("That email or password isn't right.")
                elif not resp.ok:
                    st.error(resp.text)
                else:
                    data = resp.json()
                    st.session_state.user = data
                    start_new_session()
                    st.rerun()
            except requests.exceptions.ConnectionError:
                st.error("Cannot reach FastAPI. Run `python run.py` first.")
    with tab_signup:
        with st.form("signup_form"):
            name = st.text_input("Name")
            email = st.text_input("Email", key="signup_email")
            password = st.text_input("Password", type="password", key="signup_password")
            created = st.form_submit_button("Create account", use_container_width=True)
        if created:
            try:
                resp = api(
                    "POST",
                    "/auth/signup",
                    json_body={
                        "email": email.strip(),
                        "password": password,
                        "name": name or None,
                    },
                    timeout=15,
                    auth=False,
                )
                show_response(resp)
                if resp.ok:
                    st.session_state.user = resp.json()
                    start_new_session()
                    st.rerun()
            except requests.exceptions.ConnectionError:
                st.error("Cannot reach FastAPI. Run `python run.py` first.")


# ── Pages ────────────────────────────────────────────────────────────────────


def page_companion() -> None:
    st.subheader("Companion")
    if st.session_state.get("welcome_error"):
        st.warning(st.session_state.welcome_error)

    mode = st.radio("Reply mode", ["JSON /chat/send", "SSE /chat/stream"], horizontal=True)
    c1, c2, c3 = st.columns(3)
    if c1.button("New conversation", use_container_width=True):
        start_new_session()
        st.rerun()
    if c2.button("Resume session", use_container_width=True):
        resp = api("GET", f"/chat/session/{user_id()}/resume", params={"session_id": session_id()})
        data = show_response(resp)
        if data and data.get("recent_messages"):
            st.session_state.messages = [
                {
                    "role": row.get("role") or "assistant",
                    "content": row.get("content") or "",
                    "action_cards": [],
                }
                for row in data["recent_messages"]
            ]
            if data.get("session_id"):
                st.session_state.session_id = data["session_id"]
            st.rerun()
    if c3.button("Session report", use_container_width=True):
        resp = api(
            "POST",
            "/api/session/report",
            json_body={"session_id": session_id()},
            timeout=90,
        )
        data = show_response(resp)
        if data:
            st.session_state.session_report = data

    report = st.session_state.get("session_report")
    if report:
        with st.expander("Latest session report", expanded=False):
            st.write(report.get("summary") or "")
            st.json(report)

    for msg in st.session_state.get("messages") or []:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            for card in msg.get("action_cards") or []:
                render_action_card(card)

    st.caption("Microphone → STT only (`POST /api/v1/voice/stt`). Review before send.")
    if hasattr(st, "audio_input"):
        clip = st.audio_input("Record")
    else:
        clip = st.file_uploader("WAV upload", type=["wav"])
    if clip is not None:
        clip_id = getattr(clip, "file_id", None) or getattr(clip, "name", id(clip))
        if clip_id != st.session_state.get("last_stt_clip"):
            data = clip.getvalue() if hasattr(clip, "getvalue") else bytes(clip)
            name = getattr(clip, "name", None) or "speech.wav"
            mime = getattr(clip, "type", None) or "audio/wav"
            resp = api(
                "POST",
                "/api/v1/voice/stt",
                files={"audio": (name, data, mime)},
                timeout=60,
            )
            st.session_state.last_stt_clip = clip_id
            if resp.ok:
                st.session_state.draft_box = (resp.json() or {}).get("text") or ""
            else:
                show_response(resp)

    draft = st.text_area("Draft / STT text", key="draft_box", height=80)
    if st.button("Send draft") and (draft or "").strip():
        text = draft.strip()
        try:
            with st.spinner("Listening..."):
                if mode.startswith("SSE"):
                    reply = stream_chat_text(text)
                    append_chat(text, {"reply": reply, "action_cards": []})
                else:
                    append_chat(text, send_chat_text(text))
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    user_input = st.chat_input("Talk about whatever is on your mind.")
    if user_input:
        try:
            with st.spinner("Listening..."):
                if mode.startswith("SSE"):
                    reply = stream_chat_text(user_input)
                    append_chat(user_input, {"reply": reply, "action_cards": []})
                else:
                    append_chat(user_input, send_chat_text(user_input))
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def page_mood_habits() -> None:
    st.subheader("Mood & habits")
    left, right = st.columns(2)
    with left:
        st.markdown("#### Log mood · `POST /api/mood`")
        with st.form("mood_form"):
            mood = st.text_input("Mood", value="calm")
            score = st.slider("Score", 1, 10, 5)
            note = st.text_input("Note")
            if st.form_submit_button("Save mood"):
                show_response(
                    api(
                        "POST",
                        "/api/mood",
                        json_body={
                            "mood": mood,
                            "score": score,
                            "note": note or None,
                            "client_event_id": f"st_{uuid.uuid4().hex[:8]}",
                        },
                    )
                )
        if st.button("Recent moods · GET /api/mood/recent"):
            show_response(api("GET", "/api/mood/recent", params={"days": 14}))
    with right:
        st.markdown("#### Habits")
        if st.button("List habits · GET /api/habits"):
            show_response(api("GET", "/api/habits"))
        with st.form("habit_create"):
            title = st.text_input("New habit title")
            frequency = st.selectbox("Frequency", ["daily", "weekly"])
            if st.form_submit_button("Create · POST /api/habits") and title.strip():
                show_response(
                    api(
                        "POST",
                        "/api/habits",
                        json_body={"title": title.strip(), "frequency": frequency},
                    )
                )
        habit_id = st.text_input("Habit id for check-in / patch")
        if st.button("Check in · POST /api/habits/{id}/check-in") and habit_id:
            show_response(api("POST", f"/api/habits/{habit_id}/check-in", json_body={}))
        show = st.checkbox("Show streaks to the chatbot", value=True)
        if st.button("Set streak visibility · POST /api/habits/streaks"):
            show_response(
                api("POST", "/api/habits/streaks", json_body={"show_streaks": show})
            )
        with st.form("habit_patch"):
            new_title = st.text_input("Rename habit")
            status = st.selectbox("Status", ["", "active", "paused", "archived"])
            if st.form_submit_button("Patch · PATCH /api/habits/{id}") and habit_id:
                body = {}
                if new_title.strip():
                    body["title"] = new_title.strip()
                if status:
                    body["status"] = status
                show_response(api("PATCH", f"/api/habits/{habit_id}", json_body=body))


def page_sleep() -> None:
    st.subheader("Sleep")
    with st.form("sleep_form"):
        from datetime import date as _date

        date = st.text_input("Date (YYYY-MM-DD)", value=_date.today().isoformat())
        bedtime = st.text_input("Bedtime (ISO or HH:MM)", value="23:00")
        wake = st.text_input("Wake time", value="07:00")
        minutes = st.number_input("Total minutes (optional)", min_value=0, value=0)
        if st.form_submit_button("Log sleep · POST /api/sleep"):
            body = {
                "date": date.strip(),
                "bedtime": bedtime,
                "wake_up_time": wake,
            }
            if minutes:
                body["total_duration_minutes"] = int(minutes)
            show_response(api("POST", "/api/sleep", json_body=body))
    c1, c2 = st.columns(2)
    if c1.button("Recent · GET /api/sleep/recent"):
        show_response(api("GET", "/api/sleep/recent"))
    days = c2.number_input("History days", 1, 90, 7)
    if c2.button("History · GET /api/sleep/history"):
        show_response(api("GET", "/api/sleep/history", params={"days": int(days)}))


def page_journal() -> None:
    st.subheader("Journal")
    with st.form("journal_form"):
        title = st.text_input("Title")
        content = st.text_area("Content")
        mood = st.selectbox("Mood", ["😊", "😃", "😐", "😢"])
        tags = st.text_input("Tags (comma-separated)")
        spent = st.number_input("Time spent (seconds)", min_value=0, value=0)
        if st.form_submit_button("Save · POST /journal/entry"):
            show_response(
                api(
                    "POST",
                    "/journal/entry",
                    json_body={
                        "title": title,
                        "content": content,
                        "mood": mood,
                        "tags": [t.strip() for t in tags.split(",") if t.strip()],
                        "time_spent": int(spent),
                    },
                )
            )
    cols = st.columns(3)
    if cols[0].button("Recent · GET /journal/recent-entries"):
        show_response(api("GET", "/journal/recent-entries"))
    if cols[1].button("Favorites · GET /journal/favorites"):
        show_response(api("GET", "/journal/favorites"))
    if cols[2].button("Stats · GET /journal/stats"):
        show_response(api("GET", "/journal/stats"))
    cols = st.columns(3)
    if cols[0].button("Past reflections"):
        show_response(api("GET", "/journal/past-reflections"))
    if cols[1].button("Calendar data"):
        show_response(api("GET", "/journal/calendar-data"))
    if cols[2].button("Monthly mindfulness"):
        show_response(api("GET", "/journal/monthly-mindfulness"))
    entry_id = st.text_input("Entry id")
    if st.button("Get entry · GET /journal/entry/{id}") and entry_id.strip():
        show_response(api("GET", f"/journal/entry/{entry_id.strip()}"))


def page_memory_proactive() -> None:
    st.subheader("Memory & proactive")
    left, right = st.columns(2)
    with left:
        st.markdown("#### Memory")
        enabled = st.checkbox("Personalization consent", value=True)
        if st.button("Set consent · POST /api/memory/consent"):
            show_response(
                api("POST", "/api/memory/consent", json_body={"enabled": enabled})
            )
        if st.button("Profile · GET /api/memory/profile"):
            show_response(api("GET", "/api/memory/profile"))
        if st.button("Consolidate · POST /api/memory/consolidate"):
            show_response(api("POST", "/api/memory/consolidate"))
        st.warning("Destructive memory actions")
        if st.button("Start erasure · POST /api/memory/erasure"):
            show_response(api("POST", "/api/memory/erasure"))
        if st.button("Delete adaptive memory · DELETE /api/memory"):
            show_response(api("DELETE", "/api/memory"))
        st.markdown("#### Meditation")
        if st.button("Preview · POST /api/meditation/preview"):
            show_response(api("POST", "/api/meditation/preview", json_body={}))
        med_id = st.text_input("meditation_id")
        nonce = st.text_input("execution_nonce", value=uuid.uuid4().hex)
        exec_id = st.text_input("execution_id (after start)")
        if st.button("Start · POST /api/meditation/start") and med_id and nonce:
            show_response(
                api(
                    "POST",
                    "/api/meditation/start",
                    json_body={
                        "meditation_id": med_id,
                        "execution_nonce": nonce,
                        "session_id": session_id(),
                        "reason": "streamlit",
                    },
                )
            )
        if st.button("Complete · POST /api/meditation/complete") and exec_id and nonce:
            show_response(
                api(
                    "POST",
                    "/api/meditation/complete",
                    json_body={
                        "execution_id": exec_id,
                        "execution_nonce": nonce,
                        "listen_duration_seconds": 60,
                    },
                )
            )
        feedback = st.selectbox("Meditation feedback", ["HELPFUL", "NOT_HELPFUL", "SKIPPED"])
        if st.button("Feedback · POST /api/meditation/feedback") and exec_id and nonce:
            show_response(
                api(
                    "POST",
                    "/api/meditation/feedback",
                    json_body={
                        "execution_id": exec_id,
                        "execution_nonce": nonce,
                        "feedback": feedback,
                    },
                )
            )
    with right:
        st.markdown("#### Proactive")
        opening = st.checkbox("opening_turn", value=False)
        message = st.text_input("Current message (optional)")
        if st.button("Evaluate · POST /api/proactive/evaluate"):
            show_response(
                api(
                    "POST",
                    "/api/proactive/evaluate",
                    json_body={
                        "session_id": session_id(),
                        "message": message,
                        "opening_turn": opening,
                    },
                )
            )
        if st.button("Pending · GET /api/proactive/pending"):
            show_response(api("GET", "/api/proactive/pending"))
        event_id = st.text_input("event_id")
        outcome = st.selectbox(
            "outcome", ["acknowledged", "answered", "dismissed", "ignored"]
        )
        reply = st.text_input("Reply text")
        if st.button("Respond · POST /api/proactive/respond") and event_id.strip():
            show_response(
                api(
                    "POST",
                    "/api/proactive/respond",
                    json_body={
                        "event_id": event_id.strip(),
                        "message": reply,
                        "outcome": outcome,
                    },
                )
            )


def page_exam_buddy() -> None:
    st.subheader("Exam Buddy")
    question = st.text_area("Academic question", height=120)
    if st.button("Ask · POST /api/exam-buddy/ask") and question.strip():
        show_response(
            api(
                "POST",
                "/api/exam-buddy/ask",
                json_body={"message": question.strip()},
                timeout=120,
            )
        )


def page_care_reports() -> None:
    st.subheader("Care & reports")
    left, right = st.columns(2)
    with left:
        st.markdown("#### Consultation")
        if st.button("Status · GET /consultation-evaluation/status"):
            show_response(api("GET", "/consultation-evaluation/status"))
        if st.button("Manual self · POST /consultation-evaluation/manual"):
            show_response(
                api("POST", "/consultation-evaluation/manual", json_body={"user_id": None})
            )
        st.caption("Staff-only routes below will 403 for student tokens.")
        target = st.text_input("Target user_id for staff actions")
        status = st.selectbox("Override status", ["REFERRED", "CLEARED", "WATCH"])
        reason = st.text_input("Override reason", value="Streamlit staff test")
        if st.button("Manual override (staff)"):
            show_response(
                api(
                    "POST",
                    "/consultation-evaluation/manual-override",
                    json_body={
                        "user_id": target,
                        "status": status,
                        "reason": reason,
                    },
                )
            )
        batch = st.text_area("Batch user ids (one per line)")
        if st.button("Batch (staff)"):
            ids = [line.strip() for line in batch.splitlines() if line.strip()]
            show_response(
                api(
                    "POST",
                    "/consultation-evaluation/batch",
                    json_body={"user_ids": ids},
                )
            )
    with right:
        st.markdown("#### Tasks & reports")
        if st.button("Today's tasks · GET /api/report_card/tasks/{user}"):
            show_response(api("GET", f"/api/report_card/tasks/{user_id()}"))
        title = st.text_input("Custom task title")
        if st.button("Add custom task") and title.strip():
            show_response(
                api(
                    "POST",
                    "/api/report_card/tasks/custom",
                    json_body={"title": title.strip()},
                )
            )
        task_id = st.text_input("task_id to complete / accept")
        if st.button("Complete task"):
            show_response(
                api(
                    "POST",
                    "/api/report_card/tasks/complete",
                    json_body={"task_id": task_id},
                )
            )
        if st.button("Accept report task"):
            show_response(
                api(
                    "POST",
                    "/api/reports/tasks/accept",
                    json_body={"session_id": session_id(), "task_id": task_id},
                )
            )
        patch_title = st.text_input("Patch custom task title")
        if st.button("Patch custom task") and task_id:
            show_response(
                api(
                    "PATCH",
                    f"/api/report_card/tasks/custom/{user_id()}/{task_id}",
                    json_body={"title": patch_title},
                )
            )
        if st.button("Generate session report"):
            show_response(
                api(
                    "POST",
                    "/api/session/report",
                    json_body={"session_id": session_id()},
                    timeout=90,
                )
            )


def page_system() -> None:
    st.subheader("System")
    c1, c2, c3 = st.columns(3)
    if c1.button("GET /health"):
        show_response(api("GET", "/health", auth=False, timeout=10))
    if c2.button("GET /health/live"):
        show_response(api("GET", "/health/live", auth=False, timeout=10))
    if c3.button("GET /health/ready"):
        show_response(api("GET", "/health/ready", auth=False, timeout=15))

    st.markdown("#### Preferences")
    language = st.selectbox(
        "Preferred language",
        LANGUAGES,
        index=LANGUAGES.index(
            (st.session_state.user.get("preferred_language") or "ENGLISH")
            if (st.session_state.user.get("preferred_language") or "ENGLISH") in LANGUAGES
            else 0
        ),
    )
    if st.button("Save language · POST /api/language"):
        resp = api("POST", "/api/language", json_body={"preferred_language": language})
        data = show_response(resp)
        if data:
            st.session_state.user["preferred_language"] = data.get(
                "preferred_language", language
            )
    timezone = st.text_input(
        "Timezone",
        value=st.session_state.user.get("timezone") or "Asia/Kolkata",
    )
    if st.button("Save timezone · POST /api/timezone"):
        resp = api("POST", "/api/timezone", json_body={"timezone": timezone})
        data = show_response(resp)
        if data:
            st.session_state.user["timezone"] = data.get("timezone", timezone)

    st.markdown("#### Deprecated welcome route")
    if st.button("POST /chat/welcome"):
        show_response(
            api(
                "POST",
                "/chat/welcome",
                json_body={"user_id": user_id(), "session_id": session_id()},
                timeout=90,
            )
        )


PAGE_RENDERERS = {
    "Companion": page_companion,
    "Mood & habits": page_mood_habits,
    "Sleep": page_sleep,
    "Journal": page_journal,
    "Memory & proactive": page_memory_proactive,
    "Exam Buddy": page_exam_buddy,
    "Care & reports": page_care_reports,
    "System": page_system,
}


# ── Shell ────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Zenark")
    st.session_state.api_url = st.text_input(
        "Backend API URL",
        value=st.session_state.get("api_url", DEFAULT_API),
    )
    if st.session_state.get("user"):
        user = st.session_state.user
        st.write(user.get("name") or user.get("email") or user_id())
        st.caption(user_id())
        st.caption(f"session · {session_id()}")
        page = st.radio("Section", PAGES, index=0)
        if st.button("Log out", use_container_width=True):
            for key in list(st.session_state.keys()):
                if key != "api_url":
                    st.session_state.pop(key, None)
            st.rerun()
    else:
        page = "login"
    st.divider()
    st.caption("Crisis helplines (India)")
    st.markdown(
        "- Tele-MANAS `14416`\n"
        "- Vandrevala `+91 9999 666 555`\n"
        "- KIRAN `1800-599-0019`\n"
        "- AASRA `+91 9820466726`"
    )

st.title("Zenark companion")
st.caption("Streamlit client for backend-agent APIs")

if not st.session_state.get("user"):
    page_login()
    st.stop()

if "messages" not in st.session_state or not session_id():
    start_new_session()

PAGE_RENDERERS[page]()
