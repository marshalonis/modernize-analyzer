# TODO — Modernization Analyzer

Ideas for future improvements, roughly ordered by impact vs. effort.

---

## 1. Repo Caching Between Chat Questions

**Problem:** Every question in chat mode clones the full repository from scratch, which is slow and wasteful — especially for large repos or when asking several follow-up questions.

**Idea:** Cache the cloned repo on the backend (keyed by URL + branch + credential hash) for the duration of a session or a configurable TTL (e.g. 15 minutes). Subsequent questions reuse the local clone and skip the clone step entirely.

**Files to touch:** `backend/analyzer.py`, `backend/tools.py`

---

## 2. Support Public Repos Without Authentication

**Problem:** The UI always requires a PAT or SSH key, even for fully public GitHub/GitLab repos.

**Idea:** Add a "Public repo (no auth)" option in the authentication radio. When selected, hide the credential field and pass a plain `git clone` without any credentials. This makes the tool immediately usable for OSS repos without any setup.

**Files to touch:** `frontend/app.py`, `backend/tools.py` (`clone_repository`)

---

## 3. Suggested Follow-Up Questions Based on Tech Stack

**Problem:** Users may not know what to ask after the initial analysis.

**Idea:** After a full analysis completes, parse the detected tech stack from the report and surface 3–5 contextual follow-up questions as clickable chips (e.g. if React is detected: *"Are React hooks used consistently?"*, *"Is there a state management library?"*). Clicking a chip pre-fills the chat input.

**Files to touch:** `frontend/app.py`

---

## 4. Persistent Analysis History

**Problem:** Each browser session starts fresh — past reports and Q&A are lost on refresh.

**Idea:** Store completed analyses and chat sessions in a backend database (DynamoDB or S3) keyed by a short UUID. Display a sidebar history panel with clickable past sessions. Each entry records the repo URL, timestamp, and a truncated summary. Optionally generate a shareable link.

**Files to touch:** `backend/main.py`, new `backend/history.py`, CDK for DynamoDB/S3

---

## 5. Branch / Commit Diff Analysis

**Problem:** Teams often want to understand *what changed* between two branches or releases, not just the current state of the code.

**Idea:** Add an optional second "Compare to branch" input. When provided, the agent clones both refs and focuses its analysis on the delta — new dependencies, removed tests, architecture drift, etc. — rather than a full static analysis.

**Files to touch:** `frontend/app.py`, `backend/analyzer.py`, `backend/tools.py`

---

## 6. PDF Export

**Problem:** The download button produces a `.md` file, which isn't universally readable by stakeholders.

**Idea:** Add a "Download as PDF" button that converts the markdown report to a styled PDF on the backend using a library like `weasyprint` or `md-to-pdf`. The PDF can include a cover page with the repo URL, date, and model used.

**Files to touch:** `backend/main.py`, new `backend/export.py`, `frontend/app.py`, `backend/requirements.txt`

---

## 7. Token / Cost Tracking

**Problem:** There is no visibility into how many tokens or approximate dollars each analysis costs, which makes it hard to budget or optimize.

**Idea:** Capture token usage from the Bedrock response metadata in the Strands callback handler and emit it as a final SSE event. Display a subtle "Estimated cost" badge in the UI after each run (input tokens × price + output tokens × price for the selected model).

**Files to touch:** `backend/analyzer.py` (`_StreamingHandler`), `frontend/app.py`

---

## 8. Multi-Repo Comparison

**Problem:** Users sometimes want to compare the modernization posture of two related repos (e.g. a monolith vs. its extracted service, or two team repos).

**Idea:** Add a second optional repo input. When both are filled, run two parallel analyses and render results side-by-side with a summary table highlighting where one repo leads the other (e.g. "Repo A has CI/CD, Repo B does not").

**Files to touch:** `frontend/app.py`, `backend/main.py`, `backend/analyzer.py`

---

## 9. Configurable Analysis Scope

**Problem:** The full analysis always runs every category (code quality, architecture, UI/UX), which can be noisy when a user only cares about one area.

**Idea:** Add a multi-select checklist in the sidebar to choose which analysis categories to run (e.g. "Security only", "CI/CD + Dependencies"). The selected categories are injected into the system prompt to focus the agent's attention and reduce unnecessary tool calls and token usage.

**Files to touch:** `frontend/app.py`, `backend/analyzer.py` (dynamic system prompt construction)

---

## 10. GitHub Actions / GitLab CI Integration

**Problem:** The tool is only accessible via the web UI. Teams can't easily trigger it as part of a PR review workflow.

**Idea:** Add a `/scan` REST endpoint that accepts a webhook payload (GitHub `pull_request` event or GitLab merge request hook), runs the analysis, and posts the report as a PR comment via the GitHub/GitLab API. This turns the analyzer into an automated code review bot on every PR.

**Files to touch:** `backend/main.py`, new `backend/webhook.py`, CDK (expose endpoint publicly, add secret validation)
