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
    page_title="Code Modernization Analyzer",
    page_icon="🔬",
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


def stream_analysis(payload: dict):
    """
    POST to /analyze and yield parsed SSE events as (event_type, data) tuples.
    """
    with requests.post(
        f"{BACKEND_URL}/analyze",
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
# Main — input form
# ---------------------------------------------------------------------------

st.title("🔬 Code Modernization Analyzer")
st.markdown(
    "Provide your GitLab repository details below. The AI agent will clone the repo, "
    "inspect the codebase, and produce a detailed modernization report."
)

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

run_btn = st.button("▶ Run Analysis", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Analysis execution
# ---------------------------------------------------------------------------

if run_btn:
    if not gitlab_url.strip():
        st.error("Please enter a GitLab repository URL.")
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
            events = stream_analysis(payload)

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

    # Download button
    report_text = final_report or "".join(accumulated_report)
    if report_text:
        st.divider()
        st.download_button(
            label="⬇ Download Report (Markdown)",
            data=report_text,
            file_name="modernization_report.md",
            mime="text/markdown",
        )
