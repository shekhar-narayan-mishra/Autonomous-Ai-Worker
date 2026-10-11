# Autonomous AI Task Worker

A fully autonomous browser automation agent designed for cross-application workflows using Playwright and LLM providers (Google Gemini, Groq, OpenRouter).

The agent uses **DOM text and element snapshots** (not computer vision) to inspect web interfaces, streams real-time step progress to the UI using **Server-Sent Events (SSE)** (not WebSockets), and records structured step execution logs as **JSONL traces under `runs/<id>/trace.jsonl`**.

---

## 🐍 Supported Python Version

- **Python 3.11+** (tested on 3.11, 3.12, and 3.14).
- The repo includes `.python-version` specifying the runtime version.

---

## 📦 Fresh Install & Verification

To verify that dependencies and provider SDKs install and initialize cleanly from scratch on any machine or Render:

```bash
# Automated fresh install verification script (creates isolated venv, tests imports & client construction, runs pytest)
bash scripts/fresh_install_check.sh
```

### Manual Installation Steps:
```bash
git clone <repository_url>
cd Autonomous-Ai-Worker

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install from frozen lockfile for exact reproducible dependencies
pip install -r requirements.lock.txt

# Install Playwright browser binaries and OS dependencies
playwright install --with-deps chromium
```

---

## ⚙️ Environment Profiles

The active configuration profile is selected via the `ENVIRONMENT_FILE` environment variable:

1. **Local Profile (Default):** `config/environment.local.yaml`
   - Configured for local mock services (`http://localhost:8001` for Vendor Portal, `http://localhost:8002` for ERP).
   - A fresh clone works out of the box locally with no manual edits needed.
2. **Render Deployed Profile:** `config/environment.render.yaml`
   - Configured for hosted mock applications.
   - Selected at runtime by setting `ENVIRONMENT_FILE=config/environment.render.yaml`.

---

## 🛡️ Public Deploy Safety & Rate Limiting

- **`RUN_TOKEN` Protection:** When `RUN_TOKEN` is set in the environment, `POST /run` requires a matching token via the `X-Run-Token` request header or `run_token` request body field. Unauthorized requests return HTTP 403 Forbidden.
- **`DEMO_MODE=replay_only`:** When set without `RUN_TOKEN`, live task execution via `POST /run` is disabled (returns HTTP 403 Forbidden). Users can view and inspect previous traces via `GET /replay/<id>`.
- **Pre-recorded Demos:** Offline replays are available in `demo_runs/` (e.g. `GET /replay/sample_demo`).
- **Rate Limiting:** `POST /run` allows at most **1 active run globally** and limits each client IP to **5 runs per hour** (returns HTTP 429 Too Many Requests if exceeded).
- **Secrets Masking:** Passwords, tokens, and API keys are automatically redacted in traces, history, and UI logs.

---

## 🏗️ Architecture & Execution Flow

```mermaid
graph TD
    UI[Web UI Dashboard] --> |POST /run (Token & Rate Checked)| Server[FastAPI Server]
    Server --> |SSE Stream| UI
    Server --> |Spawn| Loop[Agent Loop]
    Loop --> |Preflight Check| Chain[LLM Provider Chain]
    Chain --> |Classified Errors & Failover| Providers[Gemini / Groq / OpenRouter / Scripted]
    Loop --> |DOM Snapshot Validation| Browser[Playwright Engine]
    Browser --> |Cold Start Warmup & Auto-Retry| Apps[Vendor Portal & ERP Mock Apps]
    Loop --> |Write Steps| Trace[JSONL Trace: runs/id/trace.jsonl]
    Loop --> |Multi-Fact State| Memory[Agent Memory]
```

### Key Engineering Guardrails
1. **Error Classification & Preflight (`agent/llm.py`):**
   - Errors are classified into `config_error`, `quota_daily`, `quota_minute`, `transient`, and `bad_request`.
   - `config_error` (missing SDKs, bad constructors, missing keys) never marks entries exhausted and is never persisted to `.llm_state.json`.
   - A preflight check runs at server startup and at the beginning of each run. If no chain entry is usable, the run fails immediately with clear fix hints (e.g. `pip install google-genai`) before wasting steps.
2. **App Warmup & Auto-Retries (`agent/tools/browser.py`, `agent/loop.py`):**
   - At run start, target applications are polled for readiness (up to 90s) to absorb cold starts.
   - Navigation timeouts and 5xx errors trigger automatic retries (2 retries with backoff) at the tool level, consuming zero LLM steps and zero step budget.
   - Initial navigation uses a 60s timeout; subsequent operations use 10s.
3. **Element Snapshot Validation & Dead-End Detection:**
   - Interactive element IDs (`e1`, `e2`, etc.) are validated against the current DOM snapshot before click, type, or fill operations. Invalid IDs return actionable `wrong_approach` error hints instead of crashing Playwright.
   - HTTP errors ($\ge 400$) or non-HTML responses prompt the agent to navigate back to `last_good_url`.
4. **Strict Tool Timeouts & Watchdog:**
   - Central `asyncio.wait_for` enforces a strict 20s cap on all tool executions.
   - Per-run wall clock watchdog (default 600s) gracefully stops runaway runs with status `timeout`.
5. **Efficiency & Step Budget:**
   - LLM temperature is set to `0.0`.
   - Provider stickiness keeps the same working model across a run to prevent thrashing.
   - `save_fact` supports saving multiple key-value pairs in a single tool call.
   - Credential inquiries via `ask_human` are intercepted and redirected to `browser.login` with zero step count increment.
   - Default `max_steps`: 30. The scripted base workflow completes in $\le 14$ steps.

---

## 🧪 Testing & Plumbing Verification

All automated tests in the test suite run with fake/scripted providers (zero external LLM API calls, zero cost, completely offline):

```bash
# Run full test suite with code formatting check
bash scripts/check.sh
```

- **Plumbing Tests:** Pytest tests verify provider failover, loop state management, timeout enforcement, snapshot validation, UI regressions, and server rate-limiting without requiring live credentials.

---

## 📊 Live Results

<!-- Live evaluation benchmark results -->
**Status:** not yet run

*(To execute a live benchmark against hosted LLM providers when quota is configured, run: `python -m eval.live --tasks base --runs 1 --go`)*
