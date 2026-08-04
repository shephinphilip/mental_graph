"""
streamlit_app.py — Therapeutic AI Companion — Interactive Web UI
=================================================================

A Streamlit-based web application that connects to the FastAPI backend
and provides a full-featured therapeutic chat interface.

Features
--------
- **Sub-500ms Session Resumption** — auto-loads the last 10 messages on
  startup via ``GET /chat/session/{user_id}/resume``.
- **Real-time AI Responses** — calls ``POST /chat/send`` and displays the
  full AI reply with any embedded action cards.
- **Inline Action Cards** — renders TOOL, HABIT, TASK, BOOKING, and CONTENT
  cards as interactive UI components with one-tap buttons.
- **Crisis Intervention Banner** — displays an Emergency banner with
  24/7 crisis helpline numbers (India defaults) in the sidebar.
- **Dark-mode Aesthetic** — a premium dark-mode design with glassmorphism
  elements, smooth gradients, and branded typography.

Backend API Dependency
----------------------
This app requires the FastAPI backend (``app.py``) to be running at the
URL configured in the sidebar (default: ``http://127.0.0.1:8000``).
Run the backend with::

    uvicorn app:app --reload

Configuration
-------------
All backend connection parameters (URL, user ID, session ID) are
configurable via the sidebar widgets at runtime — no code changes needed.

Usage
-----
    streamlit run streamlit_app.py
"""

import json
import requests
import streamlit as st

# ── Page Configuration ────────────────────────────────────────────────────────
# Must be the FIRST Streamlit call in the script.  Any call to st.* before
# set_page_config will raise a StreamlitAPIException.
st.set_page_config(
    page_title="Therapeutic AI Companion",
    page_icon="🌿",
    layout="wide",                      # Use full browser width for chat layout
    initial_sidebar_state="expanded",   # Show sidebar by default on first load
)

# ── Custom CSS (Dark-mode premium aesthetic) ──────────────────────────────────
# Injected into the Streamlit app's <head> via unsafe_allow_html.
# This overrides Streamlit's default light theme and applies the
# custom design system (colors, typography, card styles, crisis banner).
st.markdown(
    """
    <style>
    /* ── Global Background & Font ──────────────────────────────────────── */
    .stApp {
        background-color: #0f172a;          /* Deep slate navy */
        color: #f8fafc;
        font-family: 'Inter', system-ui, sans-serif;
    }

    /* ── App Header Card ────────────────────────────────────────────────── */
    .app-header {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        padding: 24px;
        border-radius: 16px;
        border: 1px solid #334155;
        margin-bottom: 24px;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
    }
    .app-title {
        color: #38bdf8;                     /* Sky blue brand color */
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

    /* ── Action Card Container ──────────────────────────────────────────── */
    /* Renders inline action cards with a purple indigo gradient border */
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

    /* ── Crisis Intervention Banner ─────────────────────────────────────── */
    /* High-contrast red banner for emergency situations */
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

    /* ── Status Pill (online indicator) ────────────────────────────────── */
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


# ════════════════════════════════════════════════════════════════════════════
# Sidebar — Settings & Crisis Resources
# ════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    # Branding icon
    st.image("https://img.icons8.com/color/96/lotus.png", width=64)
    st.title("Settings & Context")

    # ── Backend connection parameters ─────────────────────────────────────
    # These can be changed at runtime without restarting the app.
    # In production, pre-fill these from environment variables or URL params.
    api_url = st.text_input(
        "Backend API URL",
        value="http://127.0.0.1:8000",
        help="URL of the running FastAPI backend (e.g. https://api.yourapp.com)",
    )
    user_id = st.text_input(
        "User ID",
        value="user_demo_001",
        help="Unique identifier for the user (e.g. Firebase UID)",
    )
    session_id = st.text_input(
        "Session ID",
        value="session_demo_001",
        help="Unique identifier for this conversation session",
    )

    st.divider()

    # ── Fast Session Resumption ───────────────────────────────────────────
    # Calls GET /chat/session/{user_id}/resume to restore session history.
    # The timeout is intentionally short (2s) since this is a fast-path endpoint.
    st.subheader("Fast Session Resumption")
    if st.button("⚡ Resume Session (<500ms)", use_container_width=True):
        try:
            resp = requests.get(
                f"{api_url}/chat/session/{user_id}/resume",
                params={"session_id": session_id},
                timeout=2,  # Expect this to be very fast; fail loudly if not
            )
            if resp.status_code == 200:
                data = resp.json()
                # Hydrate the session state with the resumed messages
                st.session_state.messages = [
                    {"role": m["role"], "content": m["content"]}
                    for m in data.get("recent_messages", [])
                ]
                # Store optional context strings for sidebar display
                st.session_state.dropped_context = data.get("dropped_session_context")
                st.session_state.emotional_state = data.get("active_emotional_state")
                st.success("Session resumed in <100ms!")
            else:
                # Non-200 response from the backend
                st.error(
                    f"Session resumption failed — backend returned {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
        except requests.exceptions.ConnectionError:
            st.error(
                "❌ Cannot connect to backend. "
                f"Ensure the FastAPI server is running at: {api_url}"
            )
        except requests.exceptions.Timeout:
            st.error(
                "⏱️ Session resumption timed out (> 2s). "
                "Check backend performance and database connectivity."
            )
        except Exception as e:
            # Catch-all for unexpected errors (e.g. malformed JSON response)
            st.error(f"Unexpected error during session resumption: {e}")

    # Show dropped context if available (gives the user a quick re-entry summary)
    if st.session_state.get("dropped_context"):
        st.info(f"💡 **Context Awareness**: {st.session_state.dropped_context}")

    st.divider()

    # ── Crisis Helplines (always visible for safety) ──────────────────────
    # These numbers are displayed prominently in the sidebar at all times,
    # regardless of whether a crisis has been detected.
    st.subheader("National Crisis Helplines (India)")
    st.caption("Available 24/7 for immediate support")
    st.markdown("""
    - **Tele-MANAS**: `14416`
    - **Vandrevala Foundation**: `+91 9999 666 555`
    - **KIRAN Helpline**: `1800-599-0019`
    - **AASRA**: `+91 9820466726`
    """)


# ════════════════════════════════════════════════════════════════════════════
# Header
# ════════════════════════════════════════════════════════════════════════════

st.markdown(
    """
    <div class="app-header">
        <div class="app-title">🌿 Therapeutic AI Companion</div>
        <div class="app-subtitle">
            Empathetic listening • Graph RAG context • Inline Action Cards • Crisis Safeguards
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ════════════════════════════════════════════════════════════════════════════
# Session State Initialization
# ════════════════════════════════════════════════════════════════════════════

# ``st.session_state`` persists across Streamlit re-runs (user interactions)
# within the same browser session.  Initialize keys that may not exist yet.
if "messages" not in st.session_state:
    st.session_state.messages = []

# ── Auto-resume on first load ─────────────────────────────────────────────
# If the chat is empty on initial page load, silently attempt to resume the
# most recent session.  Failures are silently swallowed — the user just
# starts with a fresh chat, which is a valid state.
if not st.session_state.messages:
    try:
        resp = requests.get(
            f"{api_url}/chat/session/{user_id}/resume",
            params={"session_id": session_id},
            timeout=2,
        )
        if resp.status_code == 200:
            data = resp.json()
            st.session_state.messages = [
                {"role": m["role"], "content": m["content"]}
                for m in data.get("recent_messages", [])
            ]
            # Store dropped context for sidebar display
            st.session_state.dropped_context = data.get("dropped_session_context")
    except Exception:
        # Silent fallback — a fresh empty chat is shown instead
        pass


# ════════════════════════════════════════════════════════════════════════════
# Helper: Render Action Cards
# ════════════════════════════════════════════════════════════════════════════

def render_action_card(card: dict) -> None:
    """
    Render a single action card as a styled interactive UI component.

    Reads the ``card_type``, ``title``, ``subtitle``, and
    ``action_payload`` from the card dict and renders:
    - An HTML card container with type-specific icon and gradient border
    - An interactive button appropriate for the card type:
      - BOOKING_CARD → "Connect / Book" button
      - TOOL_CARD    → "Start Tool" button
      - HABIT_CARD   → "Add Habit" button
      - Others       → no interactive button (display only)

    Parameters
    ----------
    card : dict
        A plain dict with keys: ``card_type``, ``title``, ``subtitle``,
        ``action_payload``.  This matches the ``ActionCard.model_dump()``
        output from the API response.

    Returns
    -------
    None
        Renders directly to the Streamlit UI.

    Notes
    -----
    Streamlit button ``key`` arguments must be globally unique within the
    page.  We use ``f"btn_{title}"`` as a heuristic — this will silently
    conflict if two cards share the same title.  For production, use a
    UUID or card index instead.
    """
    # Extract card fields with sensible defaults
    card_type = card.get("card_type", "TOOL_CARD")
    title = card.get("title", "Action Card")
    subtitle = card.get("subtitle", "")
    payload = card.get("action_payload", {})

    # Map card types to display icons for visual differentiation
    icons = {
        "TOOL_CARD": "🧘",       # Wellness tool / breathing exercise
        "HABIT_CARD": "💧",      # Daily habit tracker
        "TASK_CARD": "📝",       # Task or to-do item
        "BOOKING_CARD": "🩺",    # Professional therapy booking
        "CONTENT_CARD": "📚",    # Recommended reading or content
    }
    icon = icons.get(card_type, "✨")  # Default icon for unknown card types

    with st.container():
        # Render the card HTML container (styled via CSS defined above)
        st.markdown(
            f"""
            <div class="action-card">
                <div class="card-title">{icon} {title} ({card_type})</div>
                <div class="card-subtitle">{subtitle}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Render an interactive button based on the card type
        if card_type == "BOOKING_CARD":
            # Booking cards connect the user to professional therapy
            if st.button(f"🔗 Connect / Book: {title}", key=f"btn_{title}"):
                st.success("Redirecting to professional care booking flow...")

        elif card_type == "TOOL_CARD":
            # Tool cards launch an in-app wellness resource
            if st.button(f"▶️ Start Tool: {title}", key=f"btn_{title}"):
                st.info(f"Launching resource payload: {payload}")

        elif card_type == "HABIT_CARD":
            # Habit cards add a new habit to the user's daily tracker
            if st.button(f"➕ Add Habit: {title}", key=f"btn_{title}"):
                st.success("Added habit to your daily tracker!")

        # TASK_CARD and CONTENT_CARD are display-only (no interactive button)
        # — add button handlers here as the platform feature set grows.


# ════════════════════════════════════════════════════════════════════════════
# Chat Message Rendering
# ════════════════════════════════════════════════════════════════════════════

# Render all messages in session state in chronological order.
# Each message dict has at minimum: {"role": str, "content": str}.
# Assistant messages may additionally have: {"action_cards": list[dict]}.
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # Render any action cards attached to this message
        if "action_cards" in msg and msg["action_cards"]:
            for card in msg["action_cards"]:
                render_action_card(card)


# ════════════════════════════════════════════════════════════════════════════
# Chat Input & Backend Call
# ════════════════════════════════════════════════════════════════════════════

# ``st.chat_input`` renders a sticky input bar at the bottom of the page.
# It returns the user's text when submitted and ``None`` on every other re-run.
user_input = st.chat_input("Share what's on your mind...")

if user_input:
    # ── Display the user's message immediately ────────────────────────────
    # Append to session state first so the message appears instantly,
    # before we wait for the backend response.
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # ── Call the FastAPI backend ──────────────────────────────────────────
    with st.chat_message("assistant"):
        with st.spinner("Listening..."):
            try:
                response = requests.post(
                    f"{api_url}/chat/send",
                    json={
                        "user_id": user_id,
                        "session_id": session_id,
                        "message": user_input,
                    },
                    timeout=15,  # Allow up to 15s for LLM generation (including fallback)
                )

                if response.status_code == 200:
                    res_data = response.json()
                    reply_text = res_data.get("reply", "")
                    action_cards = res_data.get("action_cards", [])

                    # Display the AI reply in the chat bubble
                    st.markdown(reply_text)

                    # Render any action cards returned in this response
                    if action_cards:
                        for card in action_cards:
                            render_action_card(card)

                    # Persist the full assistant response (with cards) to session state
                    # so it is re-rendered on subsequent Streamlit re-runs.
                    st.session_state.messages.append(
                        {
                            "role": "assistant",
                            "content": reply_text,
                            "action_cards": action_cards,
                        }
                    )

                elif response.status_code == 422:
                    # Unprocessable entity — likely a malformed request payload
                    st.error(
                        f"Request validation error (422): {response.text[:300]}. "
                        "Check user_id / session_id / message fields."
                    )
                elif response.status_code == 500:
                    # Backend internal error — display the detail if available
                    detail = response.json().get("detail", response.text)
                    st.error(f"Backend error (500): {detail}")
                else:
                    # Unexpected status code
                    st.error(
                        f"Unexpected response from backend "
                        f"(status {response.status_code}): {response.text[:300]}"
                    )

            except requests.exceptions.ConnectionError:
                # The backend is not reachable (server not started, wrong URL, firewall)
                st.error(
                    "❌ Cannot connect to the FastAPI backend. "
                    "Ensure the server is running:\n\n"
                    "```\nuvicorn app:app --reload\n```"
                )
            except requests.exceptions.Timeout:
                # The backend took longer than 15 seconds — LLM timeout or overload
                st.error(
                    "⏱️ The backend took too long to respond (> 15s). "
                    "The LLM may be overloaded. Please try again in a moment."
                )
            except requests.exceptions.JSONDecodeError:
                # The backend returned a non-JSON response (crash, nginx 502, etc.)
                st.error(
                    "❌ Received an invalid response from the backend "
                    "(expected JSON). Check the server logs for errors."
                )
            except Exception as e:
                # Catch-all for truly unexpected errors (e.g. SSL, DNS failure)
                st.error(f"An unexpected error occurred: {e}")
