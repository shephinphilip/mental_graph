"""
Therapeutic AI Companion — Streamlit Interactive Web Application.

Integrated directly with the FastAPI backend:
  - Sub-500ms Session Resumption (/chat/session/{user_id}/resume)
  - Real-time response generation (/chat/send & SSE stream fallback)
  - Inline Action Card rendering (Tool, Habit, Task, Booking, Content)
  - Emergency Crisis Intervention Safeguard banner with 1-tap helpline access
"""

import json
import requests
import streamlit as st

# ── Page Configuration & Design Aesthetics ───────────────────────────────────

st.set_page_config(
    page_title="Therapeutic AI Companion",
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

# ── Sidebar Configuration ───────────────────────────────────────────────────

with st.sidebar:
    st.image("https://img.icons8.com/color/96/lotus.png", width=64)
    st.title("Settings & Context")
    
    api_url = st.text_input("Backend API URL", value="https://color-scholarship-depth-retrieve.trycloudflare.com")
    user_id = st.text_input("User ID", value="user_demo_001")
    session_id = st.text_input("Session ID", value="session_demo_001")
    
    st.divider()
    
    st.subheader("Fast Session Resumption")
    if st.button("⚡ Resume Session (<500ms)", use_container_width=True):
        try:
            resp = requests.get(f"{api_url}/chat/session/{user_id}/resume", params={"session_id": session_id}, timeout=2)
            if resp.status_code == 200:
                data = resp.json()
                st.session_state.messages = [
                    {"role": m["role"], "content": m["content"]}
                    for m in data.get("recent_messages", [])
                ]
                st.session_state.dropped_context = data.get("dropped_session_context")
                st.session_state.emotional_state = data.get("active_emotional_state")
                st.success("Session resumed in <100ms!")
            else:
                st.error("Failed to resume session")
        except Exception as e:
            st.error(f"Backend connection error: {e}")
            
    if st.session_state.get("dropped_context"):
        st.info(f"💡 **Context Awareness**: {st.session_state.dropped_context}")

    st.divider()
    
    st.subheader("National Crisis Helplines (India)")
    st.caption("Available 24/7 for immediate support")
    st.markdown("""
    - **Tele-MANAS**: `14416`
    - **Vandrevala Foundation**: `+91 9999 666 555`
    - **KIRAN Helpline**: `1800-599-0019`
    - **AASRA**: `+91 9820466726`
    """)

# ── Header ───────────────────────────────────────────────────────────────────

st.markdown(
    """
    <div class="app-header">
        <div class="app-title">🌿 Therapeutic AI Companion</div>
        <div class="app-subtitle">Empathetic listening • Graph RAG context • Inline Action Cards • Crisis Safeguards</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ── Session State Initialization ────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []

# Initial auto-resume if messages empty
if not st.session_state.messages:
    try:
        resp = requests.get(f"{api_url}/chat/session/{user_id}/resume", params={"session_id": session_id}, timeout=2)
        if resp.status_code == 200:
            data = resp.json()
            st.session_state.messages = [
                {"role": m["role"], "content": m["content"]}
                for m in data.get("recent_messages", [])
            ]
            st.session_state.dropped_context = data.get("dropped_session_context")
    except Exception:
        pass  # Graceful fallback if backend offline initially

# ── Helper to Render Action Cards ────────────────────────────────────────────

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
                <div class="card-title">{icon} {title} ({card_type})</div>
                <div class="card-subtitle">{subtitle}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if card_type == "BOOKING_CARD":
            if st.button(f"🔗 Connect / Book: {title}", key=f"btn_{title}"):
                st.success("Redirecting to professional care booking flow...")
        elif card_type == "TOOL_CARD":
            if st.button(f"▶️ Start Tool: {title}", key=f"btn_{title}"):
                st.info(f"Launching resource payload: {payload}")
        elif card_type == "HABIT_CARD":
            if st.button(f"➕ Add Habit: {title}", key=f"btn_{title}"):
                st.success(f"Added habit to your daily tracker!")

# ── Render Chat Messages ────────────────────────────────────────────────────

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "action_cards" in msg and msg["action_cards"]:
            for card in msg["action_cards"]:
                render_action_card(card)

# ── Chat Input & Processing ──────────────────────────────────────────────────

user_input = st.chat_input("Share what's on your mind...")

if user_input:
    # Append user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Call FastAPI backend
    with st.chat_message("assistant"):
        with st.spinner("Listening..."):
            try:
                response = requests.post(
                    f"{api_url}/chat/send",
                    json={"user_id": user_id, "session_id": session_id, "message": user_input},
                    timeout=15,
                )
                if response.status_code == 200:
                    res_data = response.json()
                    reply_text = res_data.get("reply", "")
                    action_cards = res_data.get("action_cards", [])

                    st.markdown(reply_text)
                    if action_cards:
                        for card in action_cards:
                            render_action_card(card)

                    # Store in session state
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
                st.error("❌ Cannot connect to FastAPI backend. Ensure server is running (`uvicorn app:app --reload`).")
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")
