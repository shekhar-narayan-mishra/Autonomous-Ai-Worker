# Development Context & Progress Log

This document records the end-to-end development history, architectural evolution, and every major problem solved in building the **Autonomous AI Task Worker** from scratch.

---

## 1. Project Overview & Initial Architecture

### Core Tech Stack
- **Backend:** Python 3.10+, FastAPI, Uvicorn, Pydantic, HTTPX.
- **Browser Automation:** Playwright (Chromium headless engine).
- **LLM Integrations:** Google Gemini (`google-genai`), Groq (`groq`), OpenRouter (`httpx`), and a local deterministic `ScriptedProvider` for zero-cost offline CI.
- **Frontend Dashboard:** Pure Vanilla HTML5, CSS3 (glassmorphic dark mode), and JavaScript communicating via Server-Sent Events (SSE).
- **Data & Mock Infrastructure:** Dual independent mock enterprise portals running on SQLite and FastAPI.

### Initial Repository Structure
```text
Autonomous-Ai-Worker/
├── agent/                  # Core autonomous agent framework
│   ├── loop.py             # Main execution loop & step controller
│   ├── llm.py              # Multi-provider client & quota failover
│   ├── env_manager.py      # Profile management, HTTP reset, preconditions
│   ├── memory.py           # Fact storage & retrieval
│   ├── reliability.py      # Error classification & loop detection
│   ├── safety.py           # Human-in-the-loop approvals & masking
│   ├── verifier.py         # Base verification classes
│   └── tools/              # Tool registry & definitions
│       ├── base.py         # Pydantic schemas & risk levels
│       ├── browser.py      # Playwright DOM snapshotting & navigation
│       ├── human.py        # ask_human tool
│       └── finish.py       # finish tool
├── config/                 # Environment profiles (local vs cloud)
│   ├── environment.yaml    # Default fallback
│   ├── environment.local.yaml  # Local ports 8001/8002
│   └── environment.render.yaml # Hosted Render URLs
├── mock_env/               # Mock enterprise applications
│   ├── vendor_portal/      # Vendor invoice portal (FastAPI + SQLite)
│   ├── erp/                # Accounting ERP system (FastAPI + SQLite)
│   ├── chaos.py            # Chaos injection engine
│   └── seed.py             # Database seeding scripts
├── verifiers/              # Independent zero-trust verifiers
│   ├── invoice_entry.py    # Cross-application HTTP verifier
│   └── report.py           # Verifier reporting utilities
├── ui/                     # Web dashboard
│   └── index.html          # Single-page interactive UI (SSE listener)
├── demo_runs/              # Pre-recorded JSONL traces for replay
├── tests/                  # 85 automated pytest test cases
├── scripts/                # Validation & verification scripts
│   ├── check.sh            # Ruff lint + full test suite
│   ├── scripted_provider.py# Scripted test provider
│   └── smoke_test.py       # Playwright browser smoke test
├── Dockerfile              # Container definition (Playwright base)
├── render.yaml             # Render cloud infrastructure blueprint
└── requirements.lock.txt   # Frozen reproducible dependency lockfile
```

---

## 2. Chronological Development Phases

### Phase 1: Foundations & Dual Mock Enterprise Applications
- **Mock Vendor Portal (`mock_env/vendor_portal`):**
  - Built an invoice repository web application with login authentication (`admin`/`password123`).
  - Added invoice tables, detail views (`/invoice/INV-101`), and search capabilities.
  - Provided REST read APIs (`GET /api/invoices`, `GET /api/invoices/{id}`) and admin reset endpoints (`POST /admin/reset`).
- **Mock Internal ERP (`mock_env/erp`):**
  - Built an accounting ERP web application with authentication (`admin`/`admin`).
  - Created bill submission forms (Vendor Name, Invoice ID, Amount, Due Date).
  - Provided REST read APIs (`GET /api/bills`) and admin reset endpoints (`POST /admin/reset`).
- **Chaos Injection Framework (`mock_env/chaos.py`):**
  - Created a dynamic middleware to inject real-world enterprise hazards:
    - `slow_load`: Artificial 5–10s response delays.
    - `flaky_500`: Intermittent HTTP 500 errors.
    - `random_popup_modal`: Disruptive modal dialogs requiring DOM dismissal.
    - `expired_session`: Force-clearing session cookies to test re-authentication.
    - `validation_error_on_first_submit`: Rejecting initial form submit to test error recovery.
    - `missing_field`: Intentionally hiding form fields.

---

### Phase 2: Agent Loop, DOM Snapshots & Tool Registry
- **DOM Accessibility Snapshotting (`agent/tools/browser.py`):**
  - Developed custom JavaScript injected via Playwright to extract interactive DOM elements.
  - Assigned sequential element IDs (`[e1]`, `[e2]`, etc.) with labels and tag names.
  - Eliminated computer vision completely, reducing token consumption by ~85% and execution latency to under 1 second per step.
- **Element ID Pre-Validation:**
  - Added safety guardrails: before clicking or typing, the agent verifies that the requested element ID exists in the current DOM snapshot.
  - Non-existent IDs return actionable `wrong_approach` error hints instead of crashing the Playwright engine.
- **Execution Budgeting & Timeouts:**
  - Set default step limit to `max_steps=30` (normal scripted flow executes in $\le 14$ steps).
  - Enforced a hard 20-second cap on every tool invocation via `asyncio.wait_for`.
  - Added a global 600-second wall-clock watchdog to terminate runaway runs.
- **State Memory & Redaction:**
  - Built `agent/memory.py` (`save_fact`, `recall`) supporting multi-fact storage in a single step.
  - Implemented automatic redaction of passwords, tokens, and secrets across logs, traces, and the UI.

---

### Phase 3: Multi-Provider LLM Chain & Quota System
- **Provider Cascade (`agent/llm.py`):**
  - Unified Google Gemini (`google-genai`), Groq (`groq`), OpenRouter (`httpx`), and `ScriptedProvider`.
  - Implemented provider stickiness: once a model produces a successful action, the agent stays with that model for the entire run to prevent context thrashing.
- **Error Classification & Cooldown Handling:**
  - Distinctly classified errors into `config_error`, `quota_daily`, `quota_minute`, `transient`, and `bad_request`.
  - Daily quota exhaustion (TPD/RPD) enters cooldown until next midnight Pacific time and persists to `.llm_state.json`.
  - Minute quota limits (TPM/RPM) apply short exponential backoffs.
  - Configuration errors (missing API key, invalid SDK) are never persisted and never trigger multi-hour lockouts.
- **Startup Preflight (`run_preflight`):**
  - Probes available provider SDKs and credentials at server startup. If no provider is available, it alerts immediately with specific installation hints (`pip install google-genai`) before burning steps.

---

### Phase 4: Server-Sent Events (SSE) & Dashboard UI
- **FastAPI Server (`server.py`):**
  - Replaced polling and WebSockets with lightweight Server-Sent Events (SSE) via `/stream/{run_id}`.
  - Added `/replay/{run_id}` supporting animated playback of completed runs from JSONL traces (`runs/<id>/trace.jsonl`).
  - Added `/health`, `/version`, and `/env` metadata endpoints.
  - Security controls: `RUN_TOKEN` enforcement, 1-active-run global limit, and 5-runs-per-hour per IP rate limiting.
- **Web Dashboard (`ui/index.html`):**
  - Responsive dark-mode interface with live browser DOM viewer.
  - Step cards displaying detailed latency breakdowns: `Provider/Model Total_ms (LLM: Xms, Tool: Yms, Wait: Zms)`.
  - Human-in-the-loop modal dialog for ambiguity clarification.
  - Independent verifier panel displaying pass/fail checklists.

---

## 3. Major Problems Encountered & Fixes Applied

### 1. The ERP Login Timeout Loop
- **Symptom:** The agent successfully extracted invoice data from the Vendor Portal, called `browser.login(app="Internal ERP")`, timed out after 10 seconds, asked human for credentials, received instructions, and repeated the failed login indefinitely until step budget exhausted.
- **Root Cause:** `browser.login` attempted to fill ERP login selectors while the browser page was still located at the Vendor Portal URL (`http://localhost:8001`).
- **Fix (`agent/tools/browser.py`):**
  - Made `browser.login` **target-aware**: compares the browser's current URL against the target application's configured `base_url`. If they do not match, it navigates to the target app first.
  - Added an already-authenticated check: if no password inputs are detected, it marks login successful immediately.
  - Intercepted redundant `ask_human` credential queries in `agent/loop.py` to prevent wasting step budget.

---

### 2. Cloud 502 Bad Gateway & Cold Starts
- **Symptom:** On Render free tier, dormant applications spun down. Navigating to them returned Render 502 Bad Gateway pages. The agent treated this as a normal page, attempted login, failed, and wasted steps.
- **Root Cause:** Transient cloud spin-up delays were treated as fatal application errors.
- **Fix (`agent/tools/browser.py`):**
  - Added target app warmup polling (up to 90s) before beginning task execution.
  - Implemented tool-level automatic retries (2 retries with backoff) for HTTP 5xx responses. Transient gateway recovery happens within Playwright without consuming LLM steps.
  - Added HTTP $\ge 400$ dead-end detection, automatically navigating back to `last_good_url`.

---

### 3. LLM False-Positive 65,680-Second Lockout
- **Symptom:** Server reported `Agent failed unexpectedly: LLM Error: All configurations exhausted. Shortest cooldown is 65680.0s (exceeds limit)`.
- **Root Cause:** Missing imports (`No module named 'google'`) and an outdated Groq client constructor (`AsyncClient.__init__() got an unexpected keyword argument 'proxies'`) were misclassified as quota exhaustion, persisting a 24-hour lockout.
- **Fix (`agent/llm.py`):**
  - Strictly separated configuration errors from quota limits. SDK import errors and bad arguments are flagged as `config_error` and never persisted.
  - Added schema sanitation: stripped `additionalProperties` from Gemini tool definitions to eliminate 400 Bad Request errors.

---

### 4. Split-Brain: Profile-Driven HTTP Verifiers & Resets
- **Symptom:** Agent executed against hosted Render applications (`vendor-portal-xr6w.onrender.com`), but the verifier and reset functions checked the local SQLite database (`mock_env/erp/erp.db`). The agent saw `INV-101` in the cloud ERP, while the verifier failed reporting `0 bills found`.
- **Root Cause:** Hardcoded localhost and direct SQLite database queries in verifiers.
- **Fix (`agent/env_manager.py`, `verifiers/invoice_entry.py`):**
  - Created a unified configuration loader: all app URLs are derived dynamically from the active environment profile (`ENVIRONMENT_FILE`).
  - Refactored `InvoiceEntryVerifier` to query HTTP APIs (`/api/invoices`, `/api/bills`) rather than opening SQLite files directly.
  - Added secure HTTP reset endpoints (`POST /admin/reset?profile=...` with `X-Admin-Token` header).
  - Added pre-run preconditions checking and ERP bill snapshotting before step 1 to prevent and detect duplicate bills.

---

### 5. Render Docker Build Failure (Python 3.10 vs SQLAlchemy 2.1.3)
- **Symptom:** Render Docker build failed on `RUN pip install -r requirements.lock.txt` with:
  `ERROR: Could not find a version that satisfies the requirement SQLAlchemy==2.1.3 (Requires-Python >=3.11)`.
- **Root Cause:** The Docker image `mcr.microsoft.com/playwright/python:v1.42.0-jammy` runs Python 3.10. An accidental dependency `SQLAlchemy==2.1.3` in the lockfile was incompatible with Python 3.10.
- **Fix (`requirements.lock.txt`):**
  - Verified that the codebase uses standard library `sqlite3` and HTTP APIs; SQLAlchemy is completely unused.
  - Removed `SQLAlchemy==2.1.3` from `requirements.lock.txt`.
  - Confirmed all remaining 48 dependencies install cleanly on Python 3.10.

---

### 6. Restoring Normal Live Execution from Replay Mode
- **Symptom:** Deployed UI showed a persistent banner `REPLAY of a recorded run — live model execution is disabled in replay demo mode` and hid execution settings, preventing live agent runs.
- **Root Cause:** `render.yaml` was configured with `DEMO_MODE=replay_only`, which instructed `server.py` to block live `POST /run` requests with 403 Forbidden and instructed `ui/index.html` to hide the live execution controls.
- **Fix (`render.yaml`, `ui/index.html`):**
  - Updated `render.yaml` to set `DEMO_MODE: ""`.
  - Removed the static replay banner and ensured `live-run-controls` remain permanently visible and enabled in `ui/index.html`.
  - Live task runs against deployed URLs are now the standard default mode.

---

## 4. Current State & Verification Metrics

- **Test Suite:** All **85 pytest test cases pass** cleanly via `scripts/check.sh`.
- **Linting:** Zero Ruff errors or formatting warnings.
- **Codebase Cleanliness:** No unused dependencies, no real LLM calls during automated testing, and full secrets masking in all outputs.
- **Production Readiness:** Successfully configured for one-click Docker deployments on Render with automatic auto-deploy on `main`.
