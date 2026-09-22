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
        if st.button("New conversation", use_container_width=True):
            start_new_session()
            st.rerun()
        if st.button("Log out", use_container_width=True):
            for key in ("user", "session_id", "messages", "welcome_error"):
                st.session_state.pop(key, None)
            st.rerun()

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
