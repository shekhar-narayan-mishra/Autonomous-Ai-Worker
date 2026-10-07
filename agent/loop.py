import asyncio
import os

import yaml
from playwright.async_api import async_playwright

from agent.events import EventBus, current_event_bus
from agent.llm import generate_action
from agent.memory import MemoryStore, recall_tool, save_fact_tool
from agent.reliability import ErrorClassification, ReliabilityManager
from agent.safety import global_human_interface
from agent.tools import browser
from agent.tools.base import RiskLevel, registry
from agent.trace import TraceLogger
from agent.verifier import get_verifier

SYSTEM_PROMPT = """Autonomous AI. Output ONLY JSON:
{"thought": "...", "action": "...", "args": {...}, "expected_outcome": "..."}

Environment:
{environment_context}

Tools:
{tool_registry}

Rules:
- Before leaving a page, save_fact every value you'll need later (ids, amounts, dates, names).
- Check Known Facts before revisiting a page. If already logged in to an app, don't log in again.
- Ask human ONLY if ambiguous, critical info missing, or blocked.
- Do NOT finish until task is submitted in ERP or confirmed missing.

Example:
{"thought": "Extracting total", "action": "save_fact", "args": {"key": "invoice_total", "value": "100.00"}, "expected_outcome": "saved"}
"""

async def run_loop(task: str, task_args: dict = None, max_steps: int = 15, event_bus: EventBus = None, run_id: str = None):
    if event_bus:
        current_event_bus.set(event_bus)
        
    os.makedirs("runs", exist_ok=True)
    if os.path.exists("trace.jsonl"):
        os.remove("trace.jsonl")
    
    trace = TraceLogger("trace.jsonl")
    memory = MemoryStore()
    
    save_fact_tool.store = memory
    recall_tool.store = memory
    
    rm = ReliabilityManager(max_steps=max_steps, max_wall_time=300)
    
    history = []
    repair_attempted = False
    
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True)
        ctx = await b.new_context()
        page = await ctx.new_page()
        browser.BrowserContext.page = page
        
        step = 1
        while step <= max_steps:
            try:
                rm.check_budgets(step)
            except Exception as e:
                print(f"Aborting: {e}")
                break
                
            try:
                with open("config/environment.yaml", "r") as f:
                    env_dict = yaml.safe_load(f)
                
                active_sessions = []
                if browser.BrowserContext.page:
                    cookies = await browser.BrowserContext.page.context.cookies()
                    cookie_domains = {c['domain'] for c in cookies}
                    
                    # Compress apps by removing verbose fields
                    for app in env_dict.get('apps', []):
                        app.pop("tools_to_use", None) # implied
                        app.pop("purpose", None) # compress
                        host = app['base_url'].replace('http://', '').replace('https://', '').split('/')[0].split(':')[0]
                        if host in cookie_domains:
                            active_sessions.append(app['name'])
                            app.pop("credentials", None) # no need if logged in
                
                env_yaml = yaml.dump(env_dict)
                if active_sessions:
                    env_yaml = "Active: " + ", ".join(active_sessions) + "\n" + env_yaml
            except FileNotFoundError:
                env_yaml = "No env config"
                
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
            
            # Print token counts
            sys_env_toks = len(sys_segment) // 4
            mem_toks = len(mem_segment) // 4
            hist_toks = sum(len(m.get("content", "")) for m in user_messages) // 4
            snap_toks = len(user_messages[-1].get("content", "")) // 4 if user_messages else 0
            
            print(f"[Prompt Tokens] Sys+Env: ~{sys_env_toks} | Memory: ~{mem_toks} | History: ~{hist_toks} | Last Snap: ~{snap_toks}")
            est_tokens_before = sys_env_toks + mem_toks + hist_toks
            
            try:
                action, metadata = await generate_action(prompt, user_messages)
            except Exception as e:
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
            
            if not tool:
                result_obs = f"Error: Tool {action.action} not found."
            else:
                if tool.risk_level in [RiskLevel.WRITE, RiskLevel.DESTRUCTIVE]:
                    approved = await global_human_interface.request_approval(action.action, action.args, f"Executing {action.action}")
                    if not approved:
                        result_obs = "Execution denied by human."
                        ok = False
                    else:
                        res = await tool.run(**action.args)
                        result_obs = res.observation
                        ok = res.ok
                else:
                    bypassed = False
                    if action.action == "ask_human":
                        q = action.args.get("question", "").lower()
                        if any(w in q for w in ["credential", "password", "username", "login", "url"]):
                            for app in env_dict.get("apps", []):
                                if app.get("name", "").lower() in q or app.get("base_url", "").lower() in q:
                                    result_obs = f"Credentials are in Available environment: {app['name']}"
                                    ok = True
                                    bypassed = True
                                    break
                    if not bypassed:
                        try:
                            res = await tool.run(**action.args)
                            result_obs = res.observation
                            ok = res.ok
                        except Exception as e:
                            err_class = rm.classify_error(e)
                            if err_class == ErrorClassification.TRANSIENT:
                                retry_reason = "Transient error detected. Retrying..."
                                print("Transient error detected. Retrying...")
                                await asyncio.sleep(2)
                                try:
                                    res = await tool.run(**action.args)
                                    result_obs = res.observation
                                    ok = res.ok
                                except Exception as e2:
                                    result_obs = f"Transient error persisted: {e2}"
                                    ok = False
                            else:
                                result_obs = str(e)
                                ok = False
            
            if run_id:
                shot_dir = f"runs/{run_id}"
            else:
                shot_dir = "runs/default"
            os.makedirs(shot_dir, exist_ok=True)
            shot_path = f"{shot_dir}/step_{step}.png"
            if browser.BrowserContext.page:
                try:
                    await browser.BrowserContext.page.screenshot(path=shot_path)
                except:
                    shot_path = None
            else:
                shot_path = None
                
            memory.history.append({
                "step": step,
                "action": action.action,
                "args": action.args,
                "observation_summary": result_obs
            })
            
            trace.log_step(step, action.thought, action.action, action.args, result_obs, ok, metadata["tokens"], metadata["latency_ms"], shot_path)
            
            if event_bus:
                await event_bus.emit("step", {
                    "step": step,
                    "thought": action.thought,
                    "action": action.action,
                    "args": action.args,
                    "observation": result_obs,
                    "latency_ms": metadata["latency_ms"],
                    "screenshot_url": f"/{shot_path}" if shot_path else None,
                    "retry_reason": retry_reason
                })

            print(f"\nStep {step}: {action.action}({action.args})")
            print(f"Observation: {result_obs[:100]}...")
            
            history.append({"role": "assistant", "content": action.model_dump_json()})
            history.append({"role": "user", "content": f"Tool execution {'succeeded' if ok else 'failed'}. Observation:\n{result_obs}"})
            
            if action.action == "finish":
                # ---------------------------------------------------------- #
                # Finish state machine:
                # finish → verifier → PASS → SUCCESS
                #                   → FAIL → one repair attempt → verifier again
                #                           → PASS → SUCCESS_AFTER_REPAIR
                #                           → FAIL → FAILED_VERIFICATION
                # Missing verifier name when one is required → VERIFICATION_ERROR
                # ---------------------------------------------------------- #
                verifier_name = task_args.get("verifier") if task_args else None
                
                if not verifier_name:
                    # No verifier configured — agent self-declares, we allow it
                    print("\n[FINISH] No verifier configured. Agent-declared completion.")
                    return {"passed": None, "status": "NO_VERIFIER", "checks": [], "evidence": {}}
                
                verifier = get_verifier(verifier_name)
                if verifier is None:
                    # Verifier name was specified but unknown — this is a config error
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
                
            step += 1
            
        await b.close()
        return {"passed": False, "status": "MAX_STEPS_REACHED", "checks": [], "evidence": {}}

