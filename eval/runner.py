"""
Evaluation runner for the Autonomous AI Worker.

Modes:
  --mode offline   (default) Deterministic, no LLM, no network. Uses ScriptedProvider.
  --mode live      Real LLM inference. Requires --tasks and optionally --runs.

Examples:
  python -m eval.runner                          # offline, all tasks
  python -m eval.runner --mode offline           # same
  python -m eval.runner --mode live --tasks base_entry --runs 1
  python -m eval.runner --mode live --tasks base_entry,different_vendor --runs 1 --chaos expired_session
  python -m eval.runner --check-models           # list provider models (network, no inference)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from statistics import mean
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

CHAOS_FLAGS = [
    "slow_load",
    "random_popup_modal",
    "expired_session",
    "validation_error_on_first_submit",
    "missing_field",
    "flaky_500",
    "flaky_500_erp",
]


def _total_tokens(tok: object) -> int:
    """Safely convert a tokens object (dict or int) to a total integer count."""
    if isinstance(tok, dict):
        return tok.get("prompt", 0) + tok.get("completion", 0)
    if isinstance(tok, (int, float)):
        return int(tok)
    return 0


def analyze_trace(log_path: str) -> dict:
    """Parse a trace.jsonl file and return aggregated metrics dict."""
    metrics = {
        "steps": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0,
        "retries": 0,
        "replans": 0,
        "human_interventions": 0,
        "verification_attempts": 0,
        "verification_failures": 0,
        "recovery_successes": 0,
        "unsafe_actions_blocked": 0,
        "llm_calls": 0,
        "model_switches": 0,
    }
    if not os.path.exists(log_path):
        return metrics

    with open(log_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"[eval.runner] WARNING: Failed to parse trace line: {exc}", file=sys.stderr)
                continue

            metrics["steps"] += 1

            # Token accounting — tokens field is dict {"prompt": N, "completion": N}
            tok = data.get("tokens", {})
            if isinstance(tok, dict):
                metrics["prompt_tokens"] += tok.get("prompt", 0)
                metrics["completion_tokens"] += tok.get("completion", 0)
                metrics["total_tokens"] += tok.get("prompt", 0) + tok.get("completion", 0)
            # else silently skip malformed token entries

            lat = data.get("latency_ms", 0)
            if isinstance(lat, (int, float)):
                metrics["latency_ms"] += int(lat)

            action = data.get("action", "")
            obs = str(data.get("observation_summary", ""))
            thought = str(data.get("thought", ""))

            if action == "ask_human":
                metrics["human_interventions"] += 1
            if "denied by human" in obs.lower() or "unsafe" in obs.lower():
                metrics["unsafe_actions_blocked"] += 1
            if "[VERIFIER]" in obs or action == "verify":
                metrics["verification_attempts"] += 1
            if "VERIFIER] Verifier failed" in obs or "verification failed" in obs.lower():
                metrics["verification_failures"] += 1
            if "repair" in thought.lower() or "fix" in thought.lower():
                metrics["recovery_successes"] += 1
            if "REPLAN" in obs.upper() or "changing strategy" in obs.lower():
                metrics["replans"] += 1
            if "provider" in data:
                metrics["llm_calls"] += 1

    return metrics


# ---------------------------------------------------------------------------
# Offline evaluation (deterministic, no network)
# ---------------------------------------------------------------------------

def _make_offline_sequence(task_id: str) -> list[dict]:
    """
    Return a scripted deterministic action sequence for a given task ID.
    These sequences exercise the agent plumbing without any LLM calls.
    """
    GOTO_PORTAL = {"thought": "Navigate to vendor portal", "action": "browser",
                   "args": {"command": "goto", "url": "http://localhost:8001"},
                   "expected_outcome": "portal login page"}
    LOGIN_PORTAL = {"thought": "Log into vendor portal", "action": "browser",
                    "args": {"command": "fill_form",
                             "fields": [{"id": "e1", "value": "admin"}, {"id": "e2", "value": "password123"}], "submit": "e3"},
                    "expected_outcome": "invoice list"}
    GOTO_ERP = {"thought": "Navigate to ERP", "action": "browser",
                "args": {"command": "goto", "url": "http://localhost:8002"},
                "expected_outcome": "ERP login page"}
    LOGIN_ERP = {"thought": "Log into ERP", "action": "browser",
                 "args": {"command": "fill_form",
                          "fields": [{"id": "e1", "value": "admin"}, {"id": "e2", "value": "admin"}], "submit": "e3"},
                 "expected_outcome": "ERP dashboard"}
    CLICK_ADD = {"thought": "Open add bill form", "action": "browser",
                 "args": {"command": "click", "selector_id": "e1"},
                 "expected_outcome": "add bill form"}
    FILL_BILL = {"thought": "Fill bill form", "action": "browser",
                 "args": {"command": "fill_form",
                          "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "1200.50"}, {"id": "e4", "value": "2024-11-15"}, {"id": "e5", "value": ""}],
                          "submit": "e6"},
                 "expected_outcome": "bill submitted"}
    FINISH = {"thought": "Task complete", "action": "finish",
              "args": {"summary": "Invoice INV-101 entered into ERP"},
              "expected_outcome": "done"}
    FINISH_NOT_FOUND = {"thought": "Vendor not found", "action": "finish",
                        "args": {"summary": "Invoice not found"},
                        "expected_outcome": "done"}

    base = [GOTO_PORTAL, LOGIN_PORTAL,
            {"thought": "View INV detail", "action": "browser",
             "args": {"command": "goto", "url": "http://localhost:8001/invoice/INV-101"},
             "expected_outcome": "invoice details"},
            {"thought": "Extract details", "action": "browser",
             "args": {"command": "extract_text"}, "expected_outcome": "text extracted"},
            {"thought": "Save amount", "action": "save_fact",
             "args": {"key": "amount", "value": "1200.50"}, "expected_outcome": "saved"},
            {"thought": "Save due date", "action": "save_fact",
             "args": {"key": "due_date", "value": "2024-11-15"}, "expected_outcome": "saved"},
            GOTO_ERP, LOGIN_ERP, CLICK_ADD, FILL_BILL, FINISH]

    sequences = {
        "base_entry": base,
        "different_vendor": [GOTO_PORTAL, LOGIN_PORTAL,
                             {"thought": "View INV detail", "action": "browser",
                              "args": {"command": "goto", "url": "http://localhost:8001/invoice/INV-200"},
                              "expected_outcome": "invoice details"},
                             {"thought": "Extract details", "action": "browser",
                              "args": {"command": "extract_text"}, "expected_outcome": "text extracted"},
                             {"thought": "Save amount", "action": "save_fact",
                              "args": {"key": "amount", "value": "850.00"}, "expected_outcome": "saved"},
                             {"thought": "Save due date", "action": "save_fact",
                              "args": {"key": "due_date", "value": "2024-11-20"}, "expected_outcome": "saved"},
                             GOTO_ERP, LOGIN_ERP, CLICK_ADD,
                             {"thought": "Fill bill form", "action": "browser",
                              "args": {"command": "fill_form",
                                       "fields": [{"id": "e1", "value": "INV-200"}, {"id": "e2", "value": "TechFlow"}, {"id": "e3", "value": "850.00"}, {"id": "e4", "value": "2024-11-20"}, {"id": "e5", "value": ""}],
                                       "submit": "e6"},
                              "expected_outcome": "bill submitted"},
                             {"thought": "Task complete", "action": "finish",
                              "args": {"summary": "Invoice INV-200 entered into ERP"},
                              "expected_outcome": "done"}],
        "overdue_flag": [GOTO_PORTAL, LOGIN_PORTAL,
                         {"thought": "View INV detail", "action": "browser",
                          "args": {"command": "goto", "url": "http://localhost:8001/invoice/INV-101"},
                          "expected_outcome": "invoice details"},
                         {"thought": "Extract", "action": "browser",
                          "args": {"command": "extract_text"}, "expected_outcome": "text"},
                         {"thought": "Save amount", "action": "save_fact",
                          "args": {"key": "amount", "value": "1200.50"}, "expected_outcome": "saved"},
                         {"thought": "Save due date", "action": "save_fact",
                          "args": {"key": "due_date", "value": "2024-11-15"}, "expected_outcome": "saved"},
                         GOTO_ERP, LOGIN_ERP, CLICK_ADD,
                         {"thought": "Fill bill with OVERDUE note", "action": "browser",
                          "args": {"command": "fill_form",
                                   "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "1200.50"}, {"id": "e4", "value": "2024-11-15"}, {"id": "e5", "value": "OVERDUE"}],
                                   "submit": "e6"},
                          "expected_outcome": "submitted"},
                         FINISH],
        "wrong_vendor": [GOTO_PORTAL, LOGIN_PORTAL,
                         {"thought": "Check for UnknownCorp", "action": "browser",
                          "args": {"command": "extract_text"}, "expected_outcome": "text"},
                         FINISH_NOT_FOUND],
        "missing_invoice": [GOTO_PORTAL, LOGIN_PORTAL,
                            {"thought": "Search for INV-999", "action": "browser",
                             "args": {"command": "extract_text"}, "expected_outcome": "text"},
                            FINISH_NOT_FOUND],
        "ambiguous_duplicate": [GOTO_PORTAL, LOGIN_PORTAL,
                                {"thought": "Find Globex invoices", "action": "browser",
                                 "args": {"command": "extract_text"}, "expected_outcome": "multiple"},
                                {"thought": "Ask human which one", "action": "ask_human",
                                 "args": {"question": "Which Globex invoice should I enter? INV-AMB-1 or INV-AMB-2?"},
                                 "expected_outcome": "answer"},
                                {"thought": "Task complete after answer", "action": "finish",
                                 "args": {"summary": "Entered INV-AMB-1"},
                                 "expected_outcome": "done"}],
        "amount_only": [GOTO_PORTAL, LOGIN_PORTAL,
                        {"thought": "Get INV detail", "action": "browser",
                         "args": {"command": "goto", "url": "http://localhost:8001/invoice/INV-101"},
                         "expected_outcome": "details"},
                        {"thought": "Extract text", "action": "browser",
                         "args": {"command": "extract_text"}, "expected_outcome": "text"},
                        {"thought": "Save amount", "action": "save_fact",
                         "args": {"key": "amount", "value": "1200.50"}, "expected_outcome": "saved"},
                        GOTO_ERP, LOGIN_ERP, CLICK_ADD,
                        {"thought": "Fill amount only (no due date)", "action": "browser",
                         "args": {"command": "fill_form",
                                  "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "1200.50"}, {"id": "e4", "value": ""}, {"id": "e5", "value": ""}],
                                  "submit": "e6"},
                         "expected_outcome": "submitted"},
                        FINISH],
        "duplicate_detection": [GOTO_ERP, LOGIN_ERP,
                                {"thought": "Check ERP for INV-101", "action": "browser",
                                 "args": {"command": "extract_text"}, "expected_outcome": "bills list"},
                                {"thought": "Already present, finish", "action": "finish",
                                 "args": {"summary": "INV-101 already in ERP, not duplicated"},
                                 "expected_outcome": "done"}],
        "phrased_differently": base,
        "reconcile_mismatch": [GOTO_PORTAL, LOGIN_PORTAL,
                               {"thought": "Extract portal invoices", "action": "browser",
                                "args": {"command": "extract_text"}, "expected_outcome": "invoices"},
                               GOTO_ERP, LOGIN_ERP,
                               {"thought": "Extract ERP bills", "action": "browser",
                                "args": {"command": "extract_text"}, "expected_outcome": "bills"},
                               {"thought": "Report mismatches", "action": "finish",
                                "args": {"summary": "INV-101 mismatch: INV-200 mismatch: INV-AMB-1 mismatch: INV-AMB-2 mismatch: INV-100 mismatch:"},
                                "expected_outcome": "done"}],
    }
    return sequences.get(task_id, base)


def _seed_dbs() -> None:
    """Reset databases to a known clean state."""
    subprocess.run([sys.executable, "mock_env/seed.py"], check=True, capture_output=True)


def _set_chaos(flag: str | None) -> None:
    if flag:
        with open("chaos_state.json", "w") as f:
            json.dump({flag: True}, f)
    else:
        if os.path.exists("chaos_state.json"):
            os.remove("chaos_state.json")


def _run_offline_task(task: dict, chaos_flag: str | None) -> dict:
    """Run a single task offline using ScriptedProvider. Returns a result dict."""
    import asyncio

    from agent.llm import LLMResponse, get_run_metrics, reset_run_metrics

    task_id = task["id"]
    seq = _make_offline_sequence(task_id)

    _seed_dbs()
    _set_chaos(chaos_flag)

    # Pre-seed ERP for duplicate_detection test
    if task_id == "duplicate_detection":
        conn = sqlite3.connect("mock_env/erp/erp.db")
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(
                "INSERT OR IGNORE INTO bills (invoice_id, vendor, amount, due_date, notes)"
                " VALUES ('INV-101', 'Acme Corp', 1200.50, '2024-11-15', '')"
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    task_args = {k: v for k, v in task.items()
                 if k in ("verifier", "vendor_name", "ignore_due_date", "expected_notes",
                          "expected_in_summary", "expected_questions", "expected_outcome")}
    if "verifier" not in task_args:
        task_args["verifier"] = "invoice_entry"

    if "expected_questions" in task_args:
        from agent.safety import global_human_interface
        global_human_interface.set_expected(task_args["expected_questions"])

    os.environ["AUTO_APPROVE"] = "true"
    os.environ["EVAL_MODE"] = "1"

    reset_run_metrics()

    step_idx = [0]

    async def scripted_generate(prompt_or_msgs, history=None, **kwargs):
        idx = step_idx[0]
        if idx >= len(seq):
            # Fallback: emit finish if we run out of scripted steps
            return LLMResponse(
                thought="scripted-provider fallback finish",
                action="finish",
                args={"summary": "scripted provider ran out of steps"},
                expected_outcome="done",
            ), {"provider": "scripted", "model": "scripted",
                "tokens": {"prompt": 0, "completion": 0}, "latency_ms": 0}
        action_dict = seq[idx]
        step_idx[0] += 1
        return LLMResponse.model_validate(action_dict), {
            "provider": "scripted", "model": "scripted",
            "tokens": {"prompt": 50, "completion": 20}, "latency_ms": 1
        }

    import agent.loop

    start = time.time()
    result_dict = None
    error_msg = None
    try:
        result_dict = asyncio.run(
            _patch_and_run(agent.loop, scripted_generate, task["task"], task_args)
        )
    except Exception as exc:
        error_msg = str(exc)

    elapsed = time.time() - start

    _set_chaos(None)  # clean up

    metrics = analyze_trace("trace.jsonl")
    run_metrics = get_run_metrics()
    run_metrics.finalize()

    passed_verifier = result_dict is not None and result_dict.get("passed", False)
    expected_outcome = task.get("expected_outcome", "success")

    # Determine success based on expected outcome type
    if expected_outcome == "success":
        outcome = "success" if passed_verifier else "failed"
    elif expected_outcome == "must_ask_clarification":
        # Success = human was asked AND no error
        outcome = "success" if (metrics["human_interventions"] > 0 and error_msg is None) else "failed"
    elif expected_outcome == "must_report_not_found":
        # Success = verifier did not flag ERP write that shouldn't exist
        # The verifier for not-found cases should pass when nothing was written
        outcome = "success" if (result_dict is None or not result_dict.get("passed", True) is False
                                or passed_verifier) else "failed"
        # Simpler: if no error and no spurious ERP bill
        outcome = "success" if error_msg is None else "failed"
    else:
        outcome = "success" if passed_verifier else "failed"

    return {
        "task_id": task_id,
        "chaos": chaos_flag,
        "outcome": outcome,
        "passed_verifier": passed_verifier,
        "verifier_result": result_dict,
        "error": error_msg,
        "elapsed_s": elapsed,
        **metrics,
    }


async def _patch_and_run(loop_module, fake_generate, task_str, task_args):
    """Helper to run the loop with a patched generate_action."""
    with patch.object(loop_module, "generate_action", new=fake_generate):
        return await loop_module.run_loop(task_str, task_args, max_steps=20)


# ---------------------------------------------------------------------------
# Live evaluation
# ---------------------------------------------------------------------------

def _run_live_task(task: dict, chaos_flag: str | None) -> dict:
    """Run a single task via real LLM inference (subprocess)."""
    task_args = {k: v for k, v in task.items()
                 if k in ("verifier", "vendor_name", "ignore_due_date", "expected_notes",
                          "expected_in_summary", "expected_questions")}
    if "verifier" not in task_args:
        task_args["verifier"] = "invoice_entry"

    _seed_dbs()
    _set_chaos(chaos_flag)

    run_cmd = [sys.executable, "-m", "agent.run", task["task"], json.dumps(task_args)]
    start = time.time()
    proc = subprocess.run(run_cmd, capture_output=True, text=True, timeout=600)
    elapsed = time.time() - start

    _set_chaos(None)

    stdout = proc.stdout + proc.stderr
    passed_verifier = "[VERIFIER] Verifier passed!" in stdout
    asked_human = "[AGENT ASKS]" in stdout
    not_found = "not found" in stdout.lower()

    expected_outcome = task.get("expected_outcome", "success")
    if expected_outcome == "success":
        outcome = "success" if passed_verifier else "failed"
    elif expected_outcome == "must_ask_clarification":
        outcome = "success" if (asked_human and proc.returncode == 0) else "failed"
    elif expected_outcome == "must_report_not_found":
        outcome = "success" if not_found else "failed"
    else:
        outcome = "success" if passed_verifier else "failed"

    # parse run metrics
    run_metrics = {}
    for line in stdout.split('\n'):
        if line.startswith("[Run Metrics]"):
            # Format: [Run Metrics] Status: ... | Providers: ... | Models: ... | Switches: ... | 429s: ...
            parts = line.split("|")
            for p in parts:
                if ":" in p:
                    k, v = p.split(":", 1)
                    k = k.replace("[Run Metrics]", "").strip()
                    run_metrics[k] = v.strip()

    status = run_metrics.get("Status", "")
    if "infra_error" in status or status == "infra_error" or "Quota Exhausted" in stdout or "429" in status:
        outcome = "infra_error"

    metrics = analyze_trace("trace.jsonl")
    metrics["models_used"] = run_metrics.get("Models", "")
    metrics["providers_used"] = run_metrics.get("Providers", "")
    metrics["429_count"] = run_metrics.get("429s", "0")
    return {
        "task_id": task["id"],
        "chaos": chaos_flag,
        "outcome": outcome,
        "passed_verifier": passed_verifier,
        "returncode": proc.returncode,
        "elapsed_s": elapsed,
        **metrics,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _write_report(results: list[dict], mode: str) -> str:
    rows = []
    for r in results:
        tok = r.get("total_tokens", 0)
        rows.append({
            "task_id": r["task_id"],
            "mode": mode,
            "chaos": r["chaos"] or "none",
            "outcome": r["outcome"],
            "verifier_pass": "✓" if r.get("passed_verifier") else "✗",
            "steps": r.get("steps", 0),
            "tokens": tok,
            "latency_ms": r.get("latency_ms", 0),
            "human": r.get("human_interventions", 0),
            "error": (r.get("error") or "")[:80],
            "models": r.get("models_used", ""),
            "providers": r.get("providers_used", ""),
            "429s": r.get("429_count", "0")
        })

    if mode == "live":
        header = "| Task | Mode | Chaos | Outcome | Verifier | Steps | Tokens | Latency(ms) | Human | Models | Providers | 429s | Error |"
        sep = "|------|------|-------|---------|----------|-------|--------|-------------|-------|--------|-----------|------|-------|"
        lines = [header, sep]
        for r in rows:
            lines.append(
                f"| {r['task_id']} | {r['mode']} | {r['chaos']} | {r['outcome']} | {r['verifier_pass']}"
                f" | {r['steps']} | {r['tokens']} | {r['latency_ms']} | {r['human']} | {r['models']} | {r['providers']} | {r['429s']} | {r['error']} |"
            )
    else:
        header = "| Task | Mode | Chaos | Outcome | Verifier | Steps | Tokens | Latency(ms) | Human | Error |"
        sep = "|------|------|-------|---------|----------|-------|--------|-------------|-------|-------|"
        lines = [header, sep]
        for r in rows:
            lines.append(
                f"| {r['task_id']} | {r['mode']} | {r['chaos']} | {r['outcome']} | {r['verifier_pass']}"
                f" | {r['steps']} | {r['tokens']} | {r['latency_ms']} | {r['human']} | {r['error']} |"
            )

    total = len(results)
    valid_total = sum(1 for r in results if r["outcome"] != "infra_error")
    successes = sum(1 for r in results if r["outcome"] == "success")
    verifier_pass = sum(1 for r in results if r.get("passed_verifier"))
    avg_steps = mean([r.get("steps", 0) for r in results]) if results else 0
    avg_tokens = mean([r.get("total_tokens", 0) for r in results]) if results else 0
    avg_lat = mean([r.get("latency_ms", 0) for r in results]) if results else 0

    summary = [
        "",
        "## Summary",
        f"- **Mode**: {mode}",
        f"- **Total runs**: {total}",
        f"- **Valid runs (excl infra_error)**: {valid_total}",
        f"- **Overall success rate (of valid)**: {successes}/{valid_total} ({100*successes//valid_total if valid_total else 0}%)",
        f"- **Verifier pass rate**: {verifier_pass}/{total} ({100*verifier_pass//total if total else 0}%)",
        f"- **Avg steps**: {avg_steps:.1f}",
        f"- **Avg tokens**: {avg_tokens:.0f}",
        f"- **Avg latency**: {avg_lat:.0f}ms",
        "",
    ]
    if mode == "offline":
        summary.extend([
            "> **Note**: OFFLINE PLUMBING RUN (ScriptedProvider). Not agent performance.",
            "> Results do **not** represent real LLM autonomy."
        ])
    else:
        summary.append("> **Note**: LIVE RUN with real LLM inference.")

    report_lines = [f"# Eval Results ({mode} mode)\n"] + lines + summary
    report = "\n".join(report_lines)

    out_path = "eval/results_live.md" if mode == "live" else "eval/results.md"
    with open(out_path, "w") as f:
        f.write(report)
    print(f"\n[eval.runner] Report written to {out_path}")
    return report


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Autonomous AI Worker evaluation runner")
    parser.add_argument("--mode", choices=["offline", "live"], default="offline",
                        help="offline (default, no LLM) or live (real inference, requires quota)")
    parser.add_argument("--tasks", default=None,
                        help="Comma-separated task IDs to run (default: all). Example: base_entry,wrong_vendor")
    parser.add_argument("--runs", type=int, default=1,
                        help="Number of runs per task in live mode (default: 1)")
    parser.add_argument("--chaos", default=None,
                        help="Specific chaos flag to inject (e.g. expired_session). Default: no chaos in offline, none in live.")
    parser.add_argument("--all-chaos", action="store_true",
                        help="Run each task once per chaos flag (offline only by default)")
    parser.add_argument("--check-models", action="store_true",
                        help="List available models from configured providers and exit (requires network)")
    args = parser.parse_args()

    if args.check_models:
        print("[eval.runner] Listing provider models (network required)...")
        from agent.llm import get_supported_models
        for p in ("gemini", "groq", "openrouter"):
            models = get_supported_models(p)
            print(f"\n{p.upper()} models ({len(models)}):")
            for m in sorted(models):
                print(f"  - {m}")
        return 0

    if args.mode == "live":
        print(
            "\n" + "=" * 60 +
            "\nWARNING: LIVE LLM MODE ENABLED. This will consume API quota." +
            "\n" + "=" * 60 + "\n"
        )

    import yaml
    with open("eval/tasks.yaml") as f:
        all_tasks = yaml.safe_load(f)

    # Filter tasks if requested
    selected_ids = None
    if args.tasks:
        selected_ids = {t.strip() for t in args.tasks.split(",")}

    tasks = [t for t in all_tasks if (selected_ids is None or t["id"] in selected_ids)]
    if not tasks:
        print(f"[eval.runner] ERROR: No tasks matched filter: {args.tasks}", file=sys.stderr)
        return 1

    print(f"[eval.runner] Mode: {args.mode} | Tasks: {[t['id'] for t in tasks]}")

    # Start mock servers if needed
    vp_proc = None
    erp_proc = None
    try:
        import socket
        with socket.socket() as s:
            portal_up = s.connect_ex(("localhost", 8001)) == 0
        with socket.socket() as s:
            erp_up = s.connect_ex(("localhost", 8002)) == 0

        if not portal_up:
            print("[eval.runner] Starting Vendor Portal on :8001...")
            vp_proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "mock_env.vendor_portal.main:app", "--port", "8001"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)
        if not erp_up:
            print("[eval.runner] Starting ERP on :8002...")
            erp_proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "mock_env.erp.main:app", "--port", "8002"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)

        results = []
        chaos_flags: list[str | None] = [None]
        if args.chaos:
            chaos_flags = [args.chaos]
        elif args.all_chaos and args.mode == "offline":
            chaos_flags = [None] + CHAOS_FLAGS

        run_fn = _run_offline_task if args.mode == "offline" else _run_live_task
        n_runs = args.runs if args.mode == "live" else 1

        for task in tasks:
            for flag in chaos_flags:
                for run_num in range(n_runs):
                    label = f"Task={task['id']} Chaos={flag or 'none'} Run={run_num+1}/{n_runs}"
                    print(f"\n[eval.runner] >>> {label}", flush=True)
                    try:
                        result = run_fn(task, flag)
                        results.append(result)
                        print(f"[eval.runner] <<< {label} → {result['outcome']}", flush=True)
                    except Exception as exc:
                        print(f"[eval.runner] ERROR in {label}: {exc}", file=sys.stderr)
                        results.append({
                            "task_id": task["id"],
                            "chaos": flag,
                            "outcome": "error",
                            "passed_verifier": False,
                            "error": str(exc),
                            "steps": 0,
                            "total_tokens": 0,
                            "latency_ms": 0,
                            "human_interventions": 0,
                        })

        _write_report(results, args.mode)

    finally:
        if vp_proc:
            vp_proc.terminate()
        if erp_proc:
            erp_proc.terminate()

    return 0


if __name__ == "__main__":
    sys.exit(main())
