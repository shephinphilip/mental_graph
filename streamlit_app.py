"""
Zenark companion — Streamlit chat.

This is freeform conversation, not a mood check-in form. The backend
resumes dropped sessions, streams replies, and renders inline action cards.
"""

import uuid
import requests
import streamlit as st

# ── Page Configuration & Design Aesthetics ───────────────────────────────────

st.set_page_config(
    page_title="Zenark — someone to talk to",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for serene, premium dark-mode aesthetic
st.markdown(
    """
    <style>
    /* Main Background & Fonts */
    .stApp {
        background-color: #0f172a;
        color: #f8fafc;
        font-family: 'Inter', system-ui, sans-serif;
    }
    
    /* Header Styling */
    .app-header {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        padding: 24px;
        border-radius: 16px;
        border: 1px solid #334155;
        margin-bottom: 24px;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
    }
    .app-title {
        color: #38bdf8;
        font-size: 28px;
        font-weight: 700;
        margin: 0;
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .app-subtitle {
        color: #94a3b8;
        font-size: 14px;
        margin-top: 6px;
    }

    /* Action Card Container */
    .action-card {
        background: linear-gradient(135deg, #1e293b 0%, #1e1b4b 100%);
        border: 1px solid #6366f1;
        border-radius: 12px;
        padding: 16px;
        margin-top: 12px;
        margin-bottom: 12px;
        box-shadow: 0 4px 12px rgba(99, 102, 241, 0.15);
    }
    .card-title {
        color: #818cf8;
        font-weight: 600;
        font-size: 16px;
        margin-bottom: 4px;
    }
    .card-subtitle {
        color: #c7d2fe;
        font-size: 13px;
        margin-bottom: 8px;
    }
    
    /* Crisis Intervention Banner */
    .crisis-banner {
        background: linear-gradient(135deg, #7f1d1d 0%, #450a0a 100%);
        border: 1px solid #ef4444;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 20px;
        color: #fef2f2;
    }
    .crisis-title {
        color: #fca5a5;
        font-size: 18px;
        font-weight: 700;
        margin-bottom: 8px;
    }

    /* Status Pill */
    .status-pill {
        background: #064e3b;
        color: #34d399;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 500;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

DEFAULT_API = "http://localhost:8000"
FALLBACK_WELCOME = (
    "I'm here. Whenever you're ready, tell me what's been sitting with you."
)


def api_base() -> str:
    return st.session_state.get("api_url", DEFAULT_API).rstrip("/")


def auth_headers() -> dict:
    token = (st.session_state.get("user") or {}).get("access_token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def send_memory_feedback(payload: dict, event_type: str) -> bool:
    if not all(
        payload.get(key)
        for key in ("edge_id", "intervention_id", "execution_nonce")
    ):
        return False
    response = requests.post(
        f"{api_base()}/api/memory/feedback",
        headers=auth_headers(),
        json={
            "edge_id": payload["edge_id"],
            "intervention_id": payload["intervention_id"],
            "execution_nonce": payload["execution_nonce"],
            "event_type": event_type,
        },
        timeout=10,
    )
    return response.ok


def send_pattern_feedback(pattern_id: str, event_type: str) -> bool:
    if not pattern_id:
        return False
    response = requests.post(
        f"{api_base()}/api/patterns/feedback",
        headers=auth_headers(),
        json={"pattern_id": pattern_id, "event_type": event_type},
        timeout=10,
    )
    return response.ok


def _meditation_api(path: str, body: dict) -> dict | None:
    response = requests.post(
        f"{api_base()}{path}",
        headers=auth_headers(),
        json=body,
        timeout=15,
    )
    if not response.ok:
        st.caption("That didn't save. You can try again.")
        return None
    return response.json()


def _meditation_binding(payload: dict) -> dict | None:
    """The card's id, nonce, and reason travel together on every write."""
    meditation_id = str(payload.get("meditation_id") or "").strip()
    nonce = str(payload.get("execution_nonce") or "").strip()
    reason = str(payload.get("reason") or payload.get("user_reason") or "").strip()
    if not meditation_id or not nonce:
        return None
    return {
        "meditation_id": meditation_id,
        "execution_nonce": nonce,
        "reason": reason,
    }


def _ensure_meditation_execution(payload: dict) -> dict | None:
    binding = _meditation_binding(payload)
    if not binding:
        st.caption("This practice card is missing its id, so it was not saved.")
        return None
    nonce = binding["execution_nonce"]
    saved = st.session_state.get(f"med_exec_{nonce}")
    if saved:
        return saved
    saved = _meditation_api(
        "/api/meditation/start",
        {
            "meditation_id": binding["meditation_id"],
            "execution_nonce": nonce,
            "reason": binding["reason"],
            "session_id": st.session_state.get("session_id"),
        },
    )
    if saved:
        st.session_state[f"med_exec_{nonce}"] = saved
    return saved


def _render_meditation_card(card: dict):
    """In-conversation practice. No scores, coordinates, or database fields."""
    from services.meditation.cards import audio_path_for_card, user_visible_fields

    view = user_visible_fields(card)
    payload = card.get("action_payload") or {}
    binding = _meditation_binding(payload) or {}
    nonce = binding.get("execution_nonce") or "unbound"
    with st.container(border=True):
        st.markdown(f"**{view['heading']}**")
        st.markdown(f"**{view['title']}**")
        if view.get("minutes"):
            st.caption(f"{view['minutes']} minutes")
        if view.get("reason"):
            st.write(view["reason"])
        audio_path = audio_path_for_card(card)
        if audio_path:
            st.audio(audio_path)
        else:
            st.caption("Audio isn't available for this practice right now.")
        start, helped, skipped = st.columns(3)
        if start.button("Start", key=f"med_start_{nonce}"):
            if _ensure_meditation_execution(payload):
                st.caption("Started. Stay with it only as long as you want.")
        if helped.button("This helped", key=f"med_help_{nonce}"):
            saved = _ensure_meditation_execution(payload)
            if saved:
                _meditation_api(
                    "/api/meditation/complete",
                    {
                        "execution_id": saved.get("execution_id"),
                        "execution_nonce": binding.get("execution_nonce"),
                        "listen_duration_seconds": payload.get("duration_seconds") or 0,
                    },
                )
                if _meditation_api(
                    "/api/meditation/feedback",
                    {
                        "execution_id": saved.get("execution_id"),
                        "execution_nonce": binding.get("execution_nonce"),
                        "feedback": "HELPFUL",
                    },
                ):
                    st.caption("Thanks. I'll remember that this was useful for you.")
        if skipped.button("Not for me", key=f"med_skip_{nonce}"):
            saved = _ensure_meditation_execution(payload)
            if saved and _meditation_api(
                "/api/meditation/feedback",
                {
                    "execution_id": saved.get("execution_id"),
                    "execution_nonce": binding.get("execution_nonce"),
                    "feedback": "NOT_HELPFUL",
                },
            ):
                st.caption("Understood. I won't treat this practice as a fit.")


def render_action_card(card: dict):
    card_type = card.get("card_type", "TOOL_CARD")
    title = card.get("title", "Action Card")
    subtitle = card.get("subtitle", "")
    payload = card.get("action_payload", {})
    icons = {
        "TOOL_CARD": "🧘",
        "HABIT_CARD": "💧",
        "TASK_CARD": "📝",
        "BOOKING_CARD": "🩺",
        "CONTENT_CARD": "📚",
    }
    icon = icons.get(card_type, "✨")
    with st.container():
        st.markdown(
            f"""
            <div class="action-card">
                <div class="card-title">{icon} {title}</div>
                <div class="card-subtitle">{subtitle}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        key = f"btn_{card_type}_{title}_{id(card)}"
        if payload.get("type") == "MEDITATION":
            _render_meditation_card(card)
            return
        is_psych = (
            payload.get("type") == "PSYCHIATRIST_REFERRAL"
            or card.get("card_id") == "card_psychiatrist_v1"
        )
        if card_type == "BOOKING_CARD" and is_psych:
            cta = card.get("cta_label") or "Explore Care Options"
            explore, dismiss = st.columns(2)
            if explore.button(cta, key=key):
                send_pattern_feedback(payload.get("pattern_id"), "STARTED")
                send_memory_feedback(payload, "STARTED")
                st.success("Opening professional care options — no pressure.")
            if dismiss.button("Not now", key=f"{key}_dismiss"):
                if send_pattern_feedback(payload.get("pattern_id"), "DISMISS"):
                    st.caption("Understood. I won’t keep offering this.")
        elif card_type == "BOOKING_CARD":
            if st.button(f"Connect / Book: {title}", key=key):
                send_memory_feedback(payload, "STARTED")
                st.success("Opening professional care booking.")
        elif card_type == "TOOL_CARD":
            if st.button(f"Start: {title}", key=key):
                send_memory_feedback(payload, "STARTED")
                st.info(f"Launching {payload}")
        elif card_type == "HABIT_CARD":
            if st.button(f"Add habit: {title}", key=key):
                send_memory_feedback(payload, "STARTED")
                st.success("Added to your habit tracker. You can dismiss this.")
        elif card_type == "TASK_CARD":
            if st.button(f"Add to today: {title}", key=key):
                send_memory_feedback(payload, "STARTED")
                st.success("Added to today's list.")
        elif card_type == "CONTENT_CARD":
            if st.button(f"Open: {title}", key=key):
                send_memory_feedback(payload, "STARTED")
                st.info(f"Opening {payload}")
        if payload.get("edge_id") and payload.get("execution_nonce"):
            helpful, not_helpful = st.columns(2)
            if helpful.button("This helped", key=f"{key}_helpful"):
                if send_memory_feedback(payload, "HELPFUL"):
                    st.success("Thanks — I’ll remember that carefully.")
            if not_helpful.button("Not for me", key=f"{key}_not_helpful"):
                if send_memory_feedback(payload, "NOT_HELPFUL"):
                    st.caption("Understood. I won’t treat this as helpful.")


def fetch_welcome(user_id: str, session_id: str) -> dict:
    response = requests.post(
        f"{api_base()}/chat/welcome",
        headers=auth_headers(),
        json={"user_id": user_id, "session_id": session_id},
        timeout=90,
    )
    response.raise_for_status()
    return response.json()


def start_new_session():
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
    except requests.exceptions.ConnectionError:
        st.session_state.welcome_error = (
            "Cannot reach the API. Start it with `python run.py`, then start a new conversation."
        )
        st.session_state.messages.append(
            {"role": "assistant", "content": FALLBACK_WELCOME, "action_cards": []}
        )
    except Exception as exc:
        st.session_state.welcome_error = str(exc)
        st.session_state.messages.append(
            {"role": "assistant", "content": FALLBACK_WELCOME, "action_cards": []}
        )


# ── Sidebar ────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image("https://img.icons8.com/color/96/lotus.png", width=64)
    st.session_state.api_url = st.text_input(
        "Backend API URL", value=st.session_state.get("api_url", DEFAULT_API)
    )

    if st.session_state.get("user"):
        user = st.session_state.user
        st.markdown(f"**{user.get('name') or user.get('email')}**")
        st.caption(
            f"{user.get('student_class') or ''} · {user.get('school') or ''} · {user.get('user_id')}"
        )
        consent = st.checkbox(
            "Personalized adaptive memory",
            value=bool(user.get("personalization_consent", False)),
            help=(
                "When enabled, Zenark can learn from actions you explicitly mark "
                "helpful. Message count and time spent are never treated as success."
            ),
        )
        languages = [
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
        current_language = user.get("preferred_language") or "ENGLISH"
        if current_language not in languages:
            current_language = "ENGLISH"
        chosen_language = st.selectbox(
            "Preferred language",
            languages,
            index=languages.index(current_language),
        )
        if chosen_language != (user.get("preferred_language") or "ENGLISH"):
            response = requests.post(
                f"{api_base()}/api/language",
                headers=auth_headers(),
                json={"language": chosen_language},
                timeout=10,
            )
            if response.ok:
                st.session_state.user["preferred_language"] = response.json().get(
                    "preferred_language", chosen_language
                )
                st.rerun()
            else:
                st.error("Could not update language.")
        if consent != bool(user.get("personalization_consent", False)):
            response = requests.post(
                f"{api_base()}/api/memory/consent",
                headers=auth_headers(),
                json={"enabled": consent},
                timeout=10,
            )
            if response.ok:
                st.session_state.user["personalization_consent"] = consent
                st.rerun()
            else:
                st.error("Could not update memory consent.")
        if st.button("Session report", use_container_width=True):
            try:
                report = requests.post(
                    f"{api_base()}/api/session/report",
                    headers=auth_headers(),
                    json={"session_id": st.session_state.get("session_id")},
                    timeout=90,
                )
                if report.ok:
                    st.session_state["session_report"] = report.json()
                else:
                    st.error(report.text)
            except requests.exceptions.ConnectionError:
                st.error("Cannot reach the API. Start it with python run.py.")
        if st.button("New conversation", use_container_width=True):
            start_new_session()
            st.rerun()
        if st.button("Log out", use_container_width=True):
            for key in ("user", "session_id", "messages", "welcome_error", "session_report"):
                st.session_state.pop(key, None)
            st.rerun()

        st.subheader("Today's tasks")
        try:
            task_response = requests.get(
                f"{api_base()}/api/report_card/tasks/{user['user_id']}",
                headers=auth_headers(),
                timeout=10,
            )
            today_tasks = task_response.json().get("tasks", []) if task_response.ok else []
        except requests.exceptions.ConnectionError:
            today_tasks = []
        if not today_tasks:
            st.caption("No tasks for today.")
        for task in today_tasks:
            label = task.get("title") or "Task"
            if task.get("completed"):
                st.caption(f"Done — {label}")
                continue
            st.write(label)
            if task.get("description"):
                st.caption(task["description"])
            if st.button("Done", key=f"task_done_{task.get('id')}", width="content"):
                try:
                    requests.post(
                        f"{api_base()}/api/report_card/tasks/complete",
                        headers=auth_headers(),
                        json={"task_id": task.get("id")},
                        timeout=10,
                    )
                    st.rerun()
                except requests.exceptions.ConnectionError:
                    st.error("Cannot reach the API. Start it with python run.py.")
        with st.form("custom_task_form", clear_on_submit=True):
            custom_title = st.text_input("Add your own task")
            add_task = st.form_submit_button("Add task", width="content")
        if add_task and custom_title.strip():
            try:
                added = requests.post(
                    f"{api_base()}/api/report_card/tasks/custom",
                    headers=auth_headers(),
                    json={"title": custom_title.strip()},
                    timeout=10,
                )
                if added.ok:
                    st.rerun()
                else:
                    st.error(added.text)
            except requests.exceptions.ConnectionError:
                st.error("Cannot reach the API. Start it with python run.py.")

        st.subheader("New Journal")
        with st.form("new_journal_form", clear_on_submit=True):
            journal_mood = st.radio(
                "How are you feeling?",
                ["😊", "😃", "😐", "😢"],
                horizontal=True,
            )
            journal_title = st.text_input("Title")
            journal_body = st.text_area("Write what's on your mind")
            journal_tags = st.text_input("Tags", placeholder="exam, stress, family")
            journal_spent = st.number_input(
                "Time spent (seconds)", min_value=0, value=0, step=30
            )
            save_journal = st.form_submit_button("Save Journal", width="stretch")
        if save_journal:
            tag_list = [part.strip() for part in journal_tags.split(",") if part.strip()]
            try:
                saved = requests.post(
                    f"{api_base()}/journal/entry",
                    headers=auth_headers(),
                    json={
                        "title": journal_title,
                        "content": journal_body,
                        "mood": journal_mood,
                        "tags": tag_list,
                        "time_spent": int(journal_spent),
                    },
                    timeout=15,
                )
                if saved.status_code == 400:
                    detail = saved.json().get("detail") if saved.headers.get("content-type", "").startswith("application/json") else saved.text
                    st.error(detail or "Check the title, writing, and mood.")
                elif saved.ok:
                    st.success("Journal saved.")
                else:
                    st.error(saved.text)
            except requests.exceptions.ConnectionError:
                st.error("Cannot reach the API. Start it with python run.py.")
        try:
            recent_journal = requests.get(
                f"{api_base()}/journal/recent-entries",
                headers=auth_headers(),
                timeout=10,
            )
            journal_rows = recent_journal.json().get("entries", []) if recent_journal.ok else []
        except requests.exceptions.ConnectionError:
            journal_rows = []
        st.markdown("**Recent journals**")
        if not journal_rows:
            st.caption("No journal entries yet.")
        for row in journal_rows:
            when = str(row.get("timestamp") or "")[:10]
            st.markdown(f"{row.get('mood') or ''} {when} — {row.get('title') or ''}")
            if row.get("content"):
                st.caption(row["content"])

    st.divider()
    st.subheader("Crisis helplines (India)")
    st.caption("Available 24/7 — Zenark is not a crisis service")
    st.markdown(
        """
    - **Tele-MANAS**: `14416`
    - **Vandrevala Foundation**: `+91 9999 666 555`
    - **KIRAN Helpline**: `1800-599-0019`
    - **AASRA**: `+91 9820466726`
    """
    )

    with st.expander("Developer demo — meditation ranking"):
        st.caption(
            "Demonstration profiles only. Internal scores stay inside "
            "Recommendation debug and are not part of the chat."
        )
        demo_accounts = {
            "Ananya — short sleep, high stress": "ananya.rao@zenark.demo",
            "Rohan — steady, longer body scans": "rohan.desai@zenark.demo",
            "Leela — low mood, kindness": "leela.nair@zenark.demo",
            "Ishaan — backlog, focus": "ishaan.mehta@zenark.demo",
            "Sara — evening, sleep": "sara.qureshi@zenark.demo",
        }
        demo_label = st.selectbox(
            "Demo user",
            list(demo_accounts),
            key="meditation_demo_label",
        )
        if st.button("Preview recommendation", key="meditation_demo_preview"):
            try:
                login = requests.post(
                    f"{api_base()}/auth/login",
                    json={
                        "email": demo_accounts[demo_label],
                        "password": "Zenark@123",
                    },
                    timeout=15,
                )
                if not login.ok:
                    st.error("Demo login failed. Run scripts/seed_meditation_demo.py first.")
                else:
                    token = login.json().get("access_token")
                    preview = requests.post(
                        f"{api_base()}/api/meditation/preview",
                        headers={"Authorization": f"Bearer {token}"},
                        json={},
                        timeout=20,
                    )
                    if preview.ok:
                        st.session_state["meditation_demo_result"] = preview.json()
                    else:
                        st.error(preview.text)
            except requests.exceptions.ConnectionError:
                st.error("Cannot reach the API. Start it with python run.py.")
        demo_result = st.session_state.get("meditation_demo_result")
        if demo_result:
            st.markdown(f"**{demo_result.get('title') or 'No practice right now'}**")
            if demo_result.get("duration_seconds"):
                minutes = max(1, round(demo_result["duration_seconds"] / 60))
                st.caption(
                    f"{demo_result.get('category') or ''} · about {minutes} minutes"
                )
            if demo_result.get("reason"):
                st.write(demo_result["reason"])
            if demo_result.get("meditation_id") and demo_result.get("audio_available"):
                from meditation.audio import get_audio_path

                audio = get_audio_path(str(demo_result["meditation_id"]))
                if audio:
                    st.audio(str(audio))
            elif demo_result.get("decision") == "NO_MEDITATION":
                st.caption("The ranker withheld a practice for this probe.")
            with st.expander("Recommendation debug"):
                st.json(demo_result.get("debug") or {})

st.markdown(
    """
    <div class="app-header">
        <div class="app-title">🌿 Zenark</div>
        <div class="app-subtitle">Someone to talk to — listens first, remembers, and stays with you across sessions</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Login ──────────────────────────────────────────────────────────────────

if not st.session_state.get("user"):
    st.subheader("Sign in")
    st.caption("Demo accounts (password for all: `Zenark@123`)")
    st.markdown(
        """
- `aarav.sharma@zenark.demo` — Class 12, prior conversation about Physics mocks

- `meera.iyer@zenark.demo` — Class 10

- `kabir.khan@zenark.demo` — Class 11
"""
    )
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Enter chat", use_container_width=True)
    if submitted:
        try:
            resp = requests.post(
                f"{api_base()}/auth/login",
                json={"email": email.strip(), "password": password},
                timeout=10,
            )
            if resp.status_code == 401:
                st.error("That email or password isn't right.")
            elif resp.status_code != 200:
                st.error(f"Login failed ({resp.status_code}): {resp.text}")
            else:
                data = resp.json()
                if data.get("password"):
                    st.error("Server leaked a password field — login aborted.")
                else:
                    st.session_state.user = data
                    start_new_session()
                    st.rerun()
        except requests.exceptions.ConnectionError:
            st.error("Cannot connect to FastAPI. Run `python run.py` in this project.")
    st.stop()

# ── Chat ───────────────────────────────────────────────────────────────────

if "messages" not in st.session_state:
    start_new_session()

if st.session_state.get("welcome_error"):
    st.warning(st.session_state.welcome_error)

session_report = st.session_state.get("session_report")
if session_report:
    with st.container(border=True):
        st.markdown("**Session report**")
        st.write(session_report.get("summary") or "")
        recommendation = session_report.get("recommendation") or {}
        card = recommendation.get("action_card")
        if card:
            _render_meditation_card(card)
        elif session_report.get("withheld_reason"):
            st.caption("No practice was added to this report.")
        report_tasks = session_report.get("tasks") or []
        if report_tasks:
            st.markdown("**Suggested for today**")
            for task in report_tasks:
                st.write(task.get("title") or "")
                if task.get("description"):
                    st.caption(task["description"])
        elif session_report.get("task_persistence") == "failed":
            st.caption("The report is saved. Today's tasks could not be updated.")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        for card in msg.get("action_cards") or []:
            render_action_card(card)

user_input = st.chat_input("Talk about whatever's on your mind. No agenda.")

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    user = st.session_state.user
    with st.chat_message("assistant"):
        with st.spinner("Listening..."):
            try:
                response = requests.post(
                    f"{api_base()}/chat/send",
                    headers=auth_headers(),
                    json={
                        "user_id": user["user_id"],
                        "session_id": st.session_state.session_id,
                        "message": user_input,
                    },
                    timeout=90,
                )
                if response.status_code == 200:
                    res_data = response.json()
                    reply_text = res_data.get("reply", "")
                    action_cards = res_data.get("action_cards", [])
                    st.markdown(reply_text)
                    for card in action_cards:
                        render_action_card(card)
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": reply_text,
                            "action_cards": action_cards,
                        }
                    )
                else:
                    st.error(f"Error {response.status_code}: {response.text}")
            except requests.exceptions.ConnectionError:
                st.error("Cannot connect to FastAPI. Ensure `python run.py` is running.")
            except Exception as exc:
                st.error(f"Something went wrong: {exc}")
