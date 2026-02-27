"""
Streamlit frontend for the Modernization Analyzer.
"""
import json
import os
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Cornhole",
    page_icon="🫖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch_available_models() -> tuple[list[dict], str]:
    try:
        resp = requests.get(f"{BACKEND_URL}/models", timeout=5)
        if resp.ok:
            data = resp.json()
            return data.get("available", []), data.get("default", "")
    except Exception:
        pass
    return [{"id": "anthropic.claude-3-5-sonnet-20241022-v2:0",
             "label": "Claude 3.5 Sonnet (recommended)"}], ""


def format_chat_as_markdown(history: list[dict], repo_url: str, branch: str) -> str:
    """Format conversation history as a readable markdown document."""
    lines = [
        "# Repository Q&A",
        f"**Repository:** {repo_url}",
        f"**Branch:** {branch}",
        "",
    ]
    pairs = []
    i = 0
    while i < len(history):
        if history[i]["role"] == "user":
            question = history[i]["content"]
            answer = history[i + 1]["content"] if i + 1 < len(history) and history[i + 1]["role"] == "assistant" else ""
            pairs.append((question, answer))
            i += 2
        else:
            i += 1
    for idx, (q, a) in enumerate(pairs, 1):
        lines += [
            "---",
            f"## Q{idx}: {q}",
            "",
            a,
            "",
        ]
    return "\n".join(lines)


def stream_events(endpoint: str, payload: dict):
    """
    POST to `endpoint` and yield parsed SSE events as (event_type, data) tuples.
    """
    with requests.post(
        f"{BACKEND_URL}{endpoint}",
        json=payload,
        stream=True,
        timeout=600,
    ) as resp:
        if not resp.ok:
            yield "error", f"Backend error {resp.status_code}: {resp.text}"
            return
        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line.startswith("data: "):
                try:
                    event = json.loads(raw_line[6:])
                    yield event.get("event", "chunk"), event.get("data", "")
                except json.JSONDecodeError:
                    yield "chunk", raw_line[6:]


# ---------------------------------------------------------------------------
# Sidebar — configuration
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("⚙️ Configuration")

    st.subheader("Model")
    models, default_model = fetch_available_models()
    model_labels = [m["label"] for m in models]
    model_ids = [m["id"] for m in models]

    default_idx = 0
    for i, mid in enumerate(model_ids):
        if mid == default_model:
            default_idx = i
            break

    selected_idx = st.selectbox(
        "Bedrock Model",
        range(len(model_labels)),
        format_func=lambda i: model_labels[i],
        index=default_idx,
    )
    selected_model_id = model_ids[selected_idx]

    st.divider()
    st.subheader("Backend")
    backend_health = st.empty()
    if st.button("Check connection"):
        try:
            r = requests.get(f"{BACKEND_URL}/health", timeout=3)
            if r.ok:
                backend_health.success(f"Connected — {r.json().get('status')}")
            else:
                backend_health.error(f"HTTP {r.status_code}")
        except Exception as e:
            backend_health.error(f"Cannot reach backend: {e}")

    st.divider()
    st.caption(f"Backend: `{BACKEND_URL}`")


# ---------------------------------------------------------------------------
# Main title
# ---------------------------------------------------------------------------

st.title("🫖 Source Code Analyzer")

# ---------------------------------------------------------------------------
# Shared repo inputs (used by both tabs)
# ---------------------------------------------------------------------------

col1, col2 = st.columns([3, 1])
with col1:
    gitlab_url = st.text_input(
        "GitLab Repository URL",
        value="https://github.com/marshalonis/modernize-analyzer.git",
        placeholder="https://gitlab.com/your-org/your-repo.git  or  git@gitlab.com:your-org/your-repo.git",
    )
with col2:
    branch = st.text_input("Branch", value="main")

auth_type = st.radio(
    "Authentication Method",
    options=["pat", "ssh"],
    format_func=lambda x: "Personal Access Token (HTTPS)" if x == "pat" else "SSH Private Key",
    horizontal=True,
)

if auth_type == "pat":
    credential = st.text_input(
        "Personal Access Token",
        type="password",
        placeholder="glpat-xxxxxxxxxxxxxxxxxxxx",
        help="GitLab PAT with at least `read_repository` scope.",
    )
else:
    credential = st.text_area(
        "SSH Private Key",
        height=150,
        placeholder="-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----",
        help="Paste the full contents of your private key file.",
    )

st.caption(f"Selected model: `{selected_model_id}`")
st.divider()

# ---------------------------------------------------------------------------
# Tabs — Analysis vs Chat
# ---------------------------------------------------------------------------

tab_analysis, tab_chat = st.tabs(["📋 Run Analysis", "💬 Ask a Question"])


# ── Analysis tab ────────────────────────────────────────────────────────────

with tab_analysis:
    st.markdown(
        "Run a full modernization analysis. The AI agent will clone the repo, "
        "inspect the codebase, and produce a detailed modernization report."
    )

    run_btn = st.button("▶ Run Analysis", type="primary", use_container_width=True)

    if run_btn:
        if not gitlab_url.strip():
            st.error("Please enter a repository URL.")
            st.stop()
        if not credential.strip():
            st.error("Please provide authentication credentials.")
            st.stop()

        payload = {
            "gitlab_url": gitlab_url.strip(),
            "auth_type": auth_type,
            "credential": credential.strip(),
            "branch": branch.strip() or "main",
            "model_id": selected_model_id,
        }

        st.divider()
        st.subheader("Analysis Progress")

        status_placeholder = st.empty()
        tool_placeholder = st.empty()
        report_container = st.container()

        with report_container:
            report_placeholder = st.empty()

        accumulated_report = []
        final_report = None

        try:
            with st.spinner("Connecting to backend..."):
                events = stream_events("/analyze", payload)

            for event_type, data in events:
                if event_type == "status":
                    status_placeholder.info(f"⏳ {data}")
                elif event_type == "tool_use":
                    tool_placeholder.caption(f"🔧 {data}")
                elif event_type == "tool_result":
                    tool_placeholder.caption("✅ Tool completed")
                elif event_type == "chunk":
                    accumulated_report.append(data)
                    report_placeholder.markdown("".join(accumulated_report))
                elif event_type == "done":
                    final_report = data
                    status_placeholder.success("✅ Analysis complete!")
                    tool_placeholder.empty()
                    report_placeholder.markdown(final_report or "".join(accumulated_report))
                elif event_type == "error":
                    status_placeholder.error(f"❌ {data}")
                    break

        except requests.exceptions.ConnectionError:
            st.error(
                f"Cannot connect to the analysis backend at `{BACKEND_URL}`. "
                "Check that the backend service is running."
            )
        except Exception as exc:
            st.error(f"Unexpected error: {exc}")

        report_text = final_report or "".join(accumulated_report)
        if report_text:
            st.divider()
            st.download_button(
                label="⬇ Download Report (Markdown)",
                data=report_text,
                file_name="modernization_report.md",
                mime="text/markdown",
            )


# ── Chat tab ─────────────────────────────────────────────────────────────────

with tab_chat:
    st.markdown(
        "Ask a specific question about the repository. "
        "The AI agent will explore the code and answer directly."
    )
    st.markdown("**Example questions:**")
    st.markdown(
        "- Does the source code have unit tests?\n"
        "- What database does this project use?\n"
        "- How is authentication handled?\n"
        "- What are the main API endpoints?\n"
        "- Is there a CI/CD pipeline configured?"
    )

    # Conversation history stored in session state
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []  # list of {"role": "user"|"assistant", "content": str}

    # Render existing conversation
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Question input
    question = st.chat_input("Ask a question about the repository...")

    if question:
        if not gitlab_url.strip():
            st.error("Please enter a repository URL above.")
            st.stop()
        if not credential.strip():
            st.error("Please provide authentication credentials above.")
            st.stop()

        # Show user message immediately
        st.session_state.chat_history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        payload = {
            "gitlab_url": gitlab_url.strip(),
            "auth_type": auth_type,
            "credential": credential.strip(),
            "branch": branch.strip() or "main",
            "model_id": selected_model_id,
            "question": question,
        }

        # Stream the assistant response
        with st.chat_message("assistant"):
            status_ph = st.empty()
            tool_ph = st.empty()
            answer_ph = st.empty()

            accumulated = []
            final_answer = None

            try:
                for event_type, data in stream_events("/chat", payload):
                    if event_type == "status":
                        status_ph.caption(f"⏳ {data}")
                    elif event_type == "tool_use":
                        tool_ph.caption(f"🔧 {data}")
                    elif event_type == "tool_result":
                        tool_ph.caption("✅ Tool completed")
                    elif event_type == "chunk":
                        accumulated.append(data)
                        answer_ph.markdown("".join(accumulated))
                    elif event_type == "done":
                        final_answer = data
                        status_ph.empty()
                        tool_ph.empty()
                        answer_ph.markdown(final_answer or "".join(accumulated))
                    elif event_type == "error":
                        status_ph.error(f"❌ {data}")
                        break

            except requests.exceptions.ConnectionError:
                st.error(
                    f"Cannot connect to the backend at `{BACKEND_URL}`. "
                    "Check that the backend service is running."
                )
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

            answer_text = final_answer or "".join(accumulated)
            if answer_text:
                st.session_state.chat_history.append(
                    {"role": "assistant", "content": answer_text}
                )

    # Download and clear buttons
    if st.session_state.chat_history:
        btn_col1, btn_col2 = st.columns([1, 1])
        with btn_col1:
            st.download_button(
                label="⬇ Download Q&A (Markdown)",
                data=format_chat_as_markdown(
                    st.session_state.chat_history,
                    gitlab_url.strip(),
                    branch.strip() or "main",
                ),
                file_name="repo_qa.md",
                mime="text/markdown",
                use_container_width=True,
            )
        with btn_col2:
            if st.button("🗑 Clear conversation", key="clear_chat", use_container_width=True):
                st.session_state.chat_history = []
                st.rerun()
