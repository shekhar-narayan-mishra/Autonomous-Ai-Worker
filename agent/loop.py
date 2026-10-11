import asyncio
import copy
import hashlib
import inspect
import os
import time

import config
from playwright.async_api import async_playwright
from agent.events import EventBus, current_event_bus
from agent.llm import generate_action, run_preflight
from agent.memory import MemoryStore, recall_tool, save_fact_tool
from agent.reliability import ReliabilityManager
from agent.safety import global_human_interface
from agent.tools import browser
from agent.tools.base import RiskLevel, ToolResult, registry
from agent.trace import TraceLogger
from agent.verifier import get_verifier

SYSTEM_PROMPT = """Autonomous AI. Output ONLY JSON:
{"thought": "...", "action": "...", "args": {...}, "expected_outcome": "..."}

Environment:
{environment_context}

Tools:
{tool_registry}

Rules:
- Before leaving a page, save_fact every value you'll need later (ids, amounts, dates, names). Save all needed facts in one save_fact call.
- Check Known Facts before revisiting a page. If already logged in to an app, don't log in again.
- Ask human ONLY if ambiguous, critical info missing, or blocked.
- Do NOT finish until task is submitted in ERP or confirmed missing.
- Every value you enter must come from an observation of the source the task names. Never infer completion from the destination system alone. In finish, cite the observation (URL/step) behind each requirement.
- To sign in to an app, use browser login with the app name.

Example:
{"thought": "Extracting facts", "action": "save_fact", "args": {"facts": {"invoice_total": "100.00", "id": "INV-101"}}, "expected_outcome": "saved"}
"""


async def execute_tool_with_timeout(tool, args: dict, cap_seconds: float = 20.0) -> ToolResult:
    """Central runner: execute any tool with central asyncio.wait_for(cap). Sync I/O goes through asyncio.to_thread."""
    try:
        if inspect.iscoroutinefunction(tool.run):
            coro = tool.run(**args)
            return await asyncio.wait_for(coro, timeout=cap_seconds)
        else:
            return await asyncio.wait_for(asyncio.to_thread(tool.run, **args), timeout=cap_seconds)
    except asyncio.TimeoutError:
        tool_name = getattr(tool, "name", "browser")
        return ToolResult(
            ok=False,
            observation=f"Timeout: tool '{tool_name}' exceeded {cap_seconds:.0f}s limit",
            error="Timeout",
            error_type="TimeoutError"
        )
    except Exception as e:
        tool_name = getattr(tool, "name", "browser")
        return ToolResult(
            ok=False,
            observation=f"Error executing {tool_name}: {e}",
            error=str(e),
            error_type=type(e).__name__
        )


async def warmup_all_apps(env_dict: dict, event_bus: EventBus = None, max_warmup_sec: float = 90.0):
    """
    At run start, warm up every app in environment.yaml (poll base_url, up to 90s, UI status "Warming up <app>").
    """
    apps = env_dict.get("apps", [])
    if not apps:
        return

    import httpx
    async with httpx.AsyncClient(timeout=5.0) as client:
        for app in apps:
            app_name = app.get("name", "App")
            base_url = app.get("base_url")
            if not base_url:
                continue

            if event_bus:
                await event_bus.emit("status_update", {"message": f"Warming up {app_name}..."})
            print(f"[Warmup] Warming up {app_name} at {base_url} (up to {max_warmup_sec:.0f}s)...")

            start_t = time.time()
            warmed = False
            consecutive_conn_errs = 0
            effective_max = 3.0 if "PYTEST_CURRENT_TEST" in os.environ else max_warmup_sec
            while time.time() - start_t < effective_max:
                try:
                    resp = await client.get(base_url)
                    if resp.status_code < 500:
                        warmed = True
                        break
                    else:
                        print(f"[Warmup] {app_name} returned HTTP {resp.status_code}, warming up...")
                except (httpx.RequestError, httpx.UnsupportedProtocol):
                    consecutive_conn_errs += 1
                    if consecutive_conn_errs >= 2:
                        print(f"[Warmup] {app_name} host unreachable ({base_url}), proceeding.")
                        break
                except Exception:
                    pass
                await asyncio.sleep(1.0)

            if warmed:
                print(f"[Warmup] {app_name} is ready.")
            else:
                print(f"[Warmup] {app_name} warm-up concluded.")


async def run_loop(task: str, task_args: dict = None, max_steps: int = 30, event_bus: EventBus = None, run_id: str = None):
    if event_bus:
        current_event_bus.set(event_bus)

    # 1. PREFLIGHT check at run start (verify SDK import and client construction)
    preflight = run_preflight()
    if event_bus:
        await event_bus.emit("chain_status", preflight)
    if not preflight.get("usable", False):
        err_msg = preflight.get("error_summary", "No configured LLM chain entry is usable.")
        print(err_msg)
        err_dict = {
            "passed": False,
            "status": "ERROR",
            "checks": [{"name": "LLM Preflight", "passed": False, "details": err_msg}],
            "evidence": {},
            "report": f"Agent preflight check failed before step 1:\n{err_msg}"
        }
        if event_bus:
            await event_bus.emit("verifier_result", err_dict)
        return err_dict

    # 2. Warm up every app in environment configuration
    env_dict = config.load_environment()
    await warmup_all_apps(env_dict, event_bus=event_bus, max_warmup_sec=90.0)

    os.makedirs("runs", exist_ok=True)
    if run_id:
        os.makedirs(f"runs/{run_id}", exist_ok=True)
        trace_path = f"runs/{run_id}/trace.jsonl"
    else:
        trace_path = "trace.jsonl"

    if os.path.exists(trace_path):
        os.remove(trace_path)

    trace = TraceLogger(trace_path)
    memory = MemoryStore()

    save_fact_tool.store = memory
    recall_tool.store = memory

    wall_cap = getattr(config, "WALL_TIMEOUT_CAP", 600.0)
    tool_cap = getattr(config, "TOOL_TIMEOUT_CAP", 20.0)
    rm = ReliabilityManager(max_steps=max_steps, max_wall_time=int(wall_cap))

    history = []
    repair_attempted = False
    run_start_time = time.time()
    watchdog_timeout = False

    browser.BrowserContext.reset()

    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = await b.new_context()
        ctx.set_default_timeout(10000)
        ctx.set_default_navigation_timeout(15000)
        page = await ctx.new_page()
        browser.BrowserContext.page = page

        step = 1
        no_progress_steps = 0
        last_obs_hash = None
        last_obs = ""
        nudge_count = 0
        done_nudge_sent = False
        loop_error = None

        while step <= max_steps:
            # Per-run wall watchdog check
            if time.time() - run_start_time >= wall_cap:
                watchdog_timeout = True
                loop_error = f"Watchdog timeout: run exceeded {wall_cap:.0f}s limit"
                print(f"Aborting: {loop_error}")
                break

            try:
                rm.check_budgets(step)
            except Exception as e:
                loop_error = f"Budget/Abort: {e}"
                print(f"Aborting: {e}")
                break

            # Reload environment dynamically
            env_dict = config.load_environment()
            env_yaml = ""
            for app in env_dict.get('apps', []):
                name = app.get('name', 'Unknown App')
                url = app.get('base_url', '')
                purpose = app.get('purpose', '')
                creds = app.get('credentials', {})

                app_line = f"{name} ({url})"
                if creds:
                    app_line += f" - sign in with: browser login app=\"{name}\""
                if purpose:
                    app_line += f" purpose: {purpose}"
                env_yaml += app_line + "\n"

            if not env_yaml:
                env_yaml = "No env config\n"

            if step in [1, 6, 12]:
                os.makedirs("docs", exist_ok=True)
                if step == 1:
                    with open("docs/prompt_example.txt", "w") as f:
                        f.write("Available environment:\n")
                        f.write(env_yaml)
                if step == 6:
                    with open("docs/prompt_step6.txt", "w") as f:
                        f.write("Available environment:\n")
                        f.write(env_yaml)

            sys_segment = SYSTEM_PROMPT.replace("{tool_registry}", registry.get_system_prompt_segment())
            sys_segment = sys_segment.replace("{environment_context}", env_yaml)
            mem_segment = memory.format_for_prompt(rolling_window_size=2)

            prompt = sys_segment + f"\n\n{mem_segment}"

            retry_reason = None
            if rm.detect_loop():
                retry_reason = "Loop detected: forced replan"
                history.append({"role": "user", "content": "You are repeating the same action. Change your strategy or ask_human."})

            recent_history = history[-8:] if len(history) > 8 else history
            user_messages = [{"role": "user", "content": f"Task: {task}"}] + recent_history

            sys_env_toks = len(sys_segment) // 4
            mem_toks = len(mem_segment) // 4
            hist_toks = sum(len(m.get("content", "")) for m in user_messages) // 4
            est_tokens_before = sys_env_toks + mem_toks + hist_toks

            try:
                action, metadata = await generate_action(prompt, user_messages)
            except Exception as e:
                loop_error = f"LLM Error: {e}"
                print(f"LLM Error: {e}")
                break

            provider_used = metadata.get("provider", "unknown")
            tokens_used = metadata.get("tokens", {})
            p_tok = tokens_used.get("prompt", est_tokens_before)
            c_tok = tokens_used.get("completion", 0)
            print(f"[Step {step} Tokens] Provider: {provider_used} | Est Input: ~{est_tokens_before} | Actual: prompt={p_tok}, completion={c_tok}")

            rm.record_action(action.action, action.args)
            save_fact_tool.current_step = step

            tool = registry.get(action.action)
            ok = False
            result_obs = ""
            bypassed_credential_ask = False

            tool_start = time.time()

            if not tool:
                result_obs = f"Error: Tool {action.action} not found."
            else:
                # Intercept credential ask_human calls before approval or execution
                if action.action == "ask_human":
                    q = str(action.args.get("question", "")).lower()
                    if any(w in q for w in ["credential", "password", "username", "login", "url"]):
                        target_name = "the application"
                        for a in env_dict.get("apps", []):
                            if a.get("name", "").lower() in q or a.get("base_url", "").lower() in q:
                                target_name = a.get("name")
                                break
                        else:
                            if env_dict.get("apps"):
                                target_name = env_dict["apps"][0].get("name", "app")

                        result_obs = f'Use browser login app="{target_name}" (Credentials are in Available environment: {target_name})'
                        ok = False
                        bypassed_credential_ask = True

                if not bypassed_credential_ask:
                    if tool.risk_level in [RiskLevel.WRITE, RiskLevel.DESTRUCTIVE]:
                        approved = await global_human_interface.request_approval(action.action, action.args, f"Executing {action.action}")
                        if not approved:
                            result_obs = "Execution denied by human."
                            ok = False
                        else:
                            res = await execute_tool_with_timeout(tool, action.args, cap_seconds=tool_cap)
                            result_obs = res.observation
                            ok = res.ok
                    else:
                        res = await execute_tool_with_timeout(tool, action.args, cap_seconds=tool_cap)
                        result_obs = res.observation
                        ok = res.ok

                        if ok and action.expected_outcome and action.expected_outcome.lower() not in result_obs.lower():
                            lower_obs = result_obs.lower()
                            if "error" in lower_obs or "invalid" in lower_obs or "incorrect" in lower_obs or "fail" in lower_obs:
                                result_obs += "\nAction failed to produce expected outcome. Page shows an error. Re-read Available environment for credentials before retrying."

            tool_ms = int((time.time() - tool_start) * 1000)

            # Record last good URL to memory if available
            if browser.BrowserContext.last_good_url:
                memory.save_fact("last_good_url", browser.BrowserContext.last_good_url, step)

            obs_hash = hashlib.md5(result_obs.encode()).hexdigest()

            is_no_progress = False
            if action.action in ["save_fact", "recall"] or obs_hash == last_obs_hash or bypassed_credential_ask or not ok:
                is_no_progress = True

            if is_no_progress:
                no_progress_steps += 1
            else:
                no_progress_steps = 0
                nudge_count = 0

            last_obs_hash = obs_hash
            last_obs = result_obs

            if no_progress_steps >= 2 and not bypassed_credential_ask:
                if nudge_count >= 1:
                    abort_reason = f"Abort: Stalled. Facts: {memory.format_for_prompt(100)}\nLast Obs: {result_obs}\nSteps: {step}"
                    print("Stalled.")
                    if not ok and action.action not in ["finish", "save_fact", "recall"] and ("error" in result_obs.lower() or "invalid" in result_obs.lower() or "incorrect" in result_obs.lower()):
                        status_str = "blocked"
                        if event_bus:
                            await event_bus.emit("step", {
                                "step": step,
                                "thought": "System blocked due to unresolved error",
                                "action": "abort",
                                "args": {},
                                "observation": abort_reason,
                                "latency_ms": 0,
                                "screenshot_url": None,
                                "retry_reason": None
                            })
                        await global_human_interface.ask("I am blocked by an unresolved error. Please help.")
                    else:
                        status_str = "stalled"
                        if event_bus:
                            await event_bus.emit("step", {
                                "step": step,
                                "thought": "System abort due to stalling",
                                "action": "abort",
                                "args": {},
                                "observation": abort_reason,
                                "latency_ms": 0,
                                "screenshot_url": None,
                                "retry_reason": None
                            })
                    err_dict = {"passed": False, "status": status_str, "checks": [], "evidence": {}, "report": abort_reason}
                    if event_bus:
                        await event_bus.emit("verifier_result", err_dict)
                    return err_dict
                else:
                    nudge_count += 1
                    result_obs += "\nSYSTEM: No progress. Take a new action or call finish."
                    no_progress_steps = 0

            if action.action != "finish" and ("done" in action.thought.lower() or "complete" in action.thought.lower() or "finish" in action.thought.lower()):
                if not done_nudge_sent:
                    result_obs += "\nSYSTEM: If complete, call finish with evidence."
                    done_nudge_sent = True

            shot_dir = f"runs/{run_id}" if run_id else "runs/default"
            os.makedirs(shot_dir, exist_ok=True)
            shot_path = f"{shot_dir}/step_{step}.png"
            if browser.BrowserContext.page:
                try:
                    await browser.BrowserContext.page.screenshot(path=shot_path)
                except Exception:
                    shot_path = None
            else:
                shot_path = None

            if hasattr(action.args, 'model_dump'):
                trace_args = copy.deepcopy(action.args.model_dump())
            elif hasattr(action.args, 'dict'):
                trace_args = copy.deepcopy(action.args.dict())
            else:
                trace_args = copy.deepcopy(action.args)

            # Redact secrets from trace and UI
            if trace_args:
                for k, v in trace_args.items():
                    if isinstance(k, str) and isinstance(v, str) and any(s in k.lower() for s in ["password", "pwd", "secret", "token", "key"]):
                        trace_args[k] = "***REDACTED***"
                if action.action == "browser":
                    if trace_args.get("command") == "type" and isinstance(trace_args.get("selector_id"), str):
                        if any(s in trace_args["selector_id"].lower() for s in ["password", "pwd"]):
                            trace_args["text"] = "***REDACTED***"
                    if trace_args.get("command") == "fill_form" and isinstance(trace_args.get("fields"), list):
                        for f in trace_args["fields"]:
                            if isinstance(f, dict):
                                fid = str(f.get("id", "")).lower()
                                if any(s in fid for s in ["password", "pwd", "secret"]):
                                    f["value"] = "***REDACTED***"

            memory.history.append({
                "step": step,
                "action": action.action,
                "args": trace_args,
                "observation_summary": result_obs
            })

            trace.log_step(step, action.thought, action.action, trace_args, result_obs, ok, metadata["tokens"], metadata["latency_ms"], metadata.get("wait_ms", 0), tool_ms, shot_path, metadata.get("attempts", []))

            if event_bus:
                await event_bus.emit("step", {
                    "step": step,
                    "thought": action.thought,
                    "action": action.action,
                    "args": trace_args,
                    "observation": result_obs,
                    "latency_ms": metadata["latency_ms"],
                    "wait_ms": metadata.get("wait_ms", 0),
                    "wait_reasons": metadata.get("wait_reasons", []),
                    "tool_ms": tool_ms,
                    "provider": metadata.get("provider", "unknown"),
                    "model": metadata.get("model", "unknown"),
                    "screenshot_url": f"/{shot_path}" if shot_path else None,
                    "retry_reason": retry_reason
                })

            print(f"\nStep {step}: {action.action}({action.args})")
            print(f"Observation: {result_obs[:100]}...")

            history.append({"role": "assistant", "content": action.model_dump_json()})
            history.append({"role": "user", "content": f"Tool execution {'succeeded' if ok else 'failed'}. Observation:\n{result_obs}"})

            if action.action == "finish" and ok:
                verifier_name = task_args.get("verifier") if task_args else None
                if not verifier_name:
                    print("\n[FINISH] No verifier configured. Agent-declared completion.")
                    return {"passed": None, "status": "NO_VERIFIER", "checks": [], "evidence": {}}

                verifier = get_verifier(verifier_name)
                if verifier is None:
                    msg = f"VERIFICATION_ERROR: verifier '{verifier_name}' is not registered."
                    print(f"\n[VERIFIER] {msg}")
                    err_dict = {"passed": False, "status": "VERIFICATION_ERROR",
                                "checks": [{"name": "Verifier found", "passed": False, "details": msg}],
                                "evidence": {}}
                    if event_bus:
                        await event_bus.emit("verifier_result", err_dict)
                    return err_dict

                print("\n[VERIFIER] Running independent verification...")
                v_res = verifier.verify(task_args)
                v_res_dict = v_res.model_dump()
                v_res_dict["status"] = "SUCCESS" if v_res.passed else "FAILED_VERIFICATION"

                if event_bus:
                    await event_bus.emit("verifier_result", v_res_dict)
                print(f"[VERIFIER] result: {v_res.model_dump_json(indent=2)}")

                if not v_res.passed and not repair_attempted:
                    print("[VERIFIER] Verification failed. Giving agent one repair attempt...")
                    repair_attempted = True
                    history.append({"role": "user", "content": (
                        f"VERIFICATION FAILED. The verifier found issues:\n"
                        f"{v_res.model_dump_json()}\n"
                        f"Please fix the issue(s) listed above and call finish again."
                    )})
                    step += 1
                    continue
                elif not v_res.passed and repair_attempted:
                    print("[VERIFIER] Verification failed after repair attempt. Status: FAILED_VERIFICATION")
                    v_res_dict["status"] = "FAILED_VERIFICATION"
                    return v_res_dict
                else:
                    status = "SUCCESS_AFTER_REPAIR" if repair_attempted else "SUCCESS"
                    print(f"[VERIFIER] Verification passed! Status: {status}")
                    v_res_dict["status"] = status
                    return v_res_dict

            # A blocked credential ask_human does NOT count as a step
            if not bypassed_credential_ask:
                step += 1

        await b.close()

        # Determine terminal status: "Max Steps Reached" appears ONLY when steps >= max_steps
        if watchdog_timeout:
            status = "timeout"
            report = f"Run exceeded wall-clock limit of {wall_cap:.0f}s.\nPartial facts: {memory.format_for_prompt(100)}\nLast Obs: {last_obs}"
        elif loop_error:
            status = "ERROR"
            report = f"Agent failed unexpectedly: {loop_error}\nLast Obs: {last_obs}"
        elif step >= max_steps:
            status = "MAX_STEPS_REACHED"
            report = f"Facts: {memory.format_for_prompt(100)}\nLast Obs: {last_obs}"
        else:
            status = "ERROR"
            report = f"Run terminated early at step {step}.\nLast Obs: {last_obs}"

        err_dict = {
            "passed": False,
            "status": status,
            "checks": [],
            "evidence": {},
            "report": report
        }
        if event_bus:
            await event_bus.emit("verifier_result", err_dict)
        return err_dict
