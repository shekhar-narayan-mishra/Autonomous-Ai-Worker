import asyncio
import os
import json
from agent.tools.base import registry, RiskLevel
from agent.tools import browser, files, http_api, finish, human
from agent.llm import generate_action
from agent.trace import TraceLogger
from agent.memory import MemoryStore, save_fact_tool, recall_tool
from agent.reliability import ReliabilityManager, ErrorClassification
from agent.safety import global_human_interface
from agent.verifier import get_verifier
from playwright.async_api import async_playwright

import yaml
from agent.events import current_event_bus, EventBus

SYSTEM_PROMPT = """You are an Autonomous AI Task Worker.
Your goal is to complete the user's task using the available tools.
You must output ONLY valid JSON matching this schema:
{
  "thought": "Your reasoning based on the last observation",
  "action": "tool_name",
  "args": {"arg1": "val1"},
  "expected_outcome": "What you expect to happen"
}

Available environment:
{environment_context}

Available tools:
{tool_registry}

Guidelines:
- CRITICAL: You must execute the task yourself using the browser tool.
- ASK POLICY: Ask the human ONLY when (a) multiple candidates match and it's ambiguous, (b) required info is missing and cannot be found with any tool, (c) an action needs explicit approval, or (d) you are blocked after retries.
- NEVER ask for info discoverable via the environment config (like URLs or credentials) or tools.
- Use 'save_fact' to store important extracted info (e.g. amount, due date) so you don't lose it.
- If a form submit fails, read the error and correct it.
- When you are done, call 'finish' with a summary.
- If an invoice is missing, report "not found" via finish and do not fabricate data.
"""

async def run_loop(task: str, task_args: dict = None, max_steps: int = 15, event_bus: EventBus = None, run_id: str = None):
    if event_bus:
        current_event_bus.set(event_bus)
        
    os.makedirs("screenshots", exist_ok=True)
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
                    env_yaml = f.read()
            except FileNotFoundError:
                env_yaml = "No environment config provided."
            prompt = SYSTEM_PROMPT.replace("{tool_registry}", registry.get_system_prompt_segment())
            prompt = prompt.replace("{environment_context}", env_yaml)
            prompt += f"\n\n{memory.format_for_prompt()}"
            
            if rm.detect_loop():
                history.append({"role": "user", "content": "You are repeating the same action. Change your strategy or ask_human."})
            
            try:
                action, metadata = await generate_action(prompt, [{"role": "user", "content": f"Task: {task}"}] + history)
            except Exception as e:
                print(f"LLM Error: {e}")
                break
                
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
                    try:
                        res = await tool.run(**action.args)
                        result_obs = res.observation
                        ok = res.ok
                    except Exception as e:
                        err_class = rm.classify_error(e)
                        if err_class == ErrorClassification.TRANSIENT:
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
            
            shot_name = f"{run_id}_step_{step}.png" if run_id else f"step_{step}.png"
            shot_path = f"screenshots/{shot_name}"
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
                    "screenshot_url": f"/screenshots/{shot_name}" if shot_path else None
                })

            print(f"\nStep {step}: {action.action}({action.args})")
            print(f"Observation: {result_obs[:100]}...")
            
            history.append({"role": "assistant", "content": action.model_dump_json()})
            history.append({"role": "user", "content": f"Tool execution {'succeeded' if ok else 'failed'}. Observation:\n{result_obs}"})
            
            if action.action == "finish":
                v_res_dict = None
                if task_args and "verifier" in task_args:
                    verifier = get_verifier(task_args["verifier"])
                    if verifier:
                        print("\n[VERIFIER] Running verifier...")
                        v_res = verifier.verify(task_args)
                        v_res_dict = v_res.model_dump()
                        if event_bus:
                            await event_bus.emit("verifier_result", v_res_dict)
                        print(f"[VERIFIER] result: {v_res.model_dump_json(indent=2)}")
                        if not v_res.passed and not repair_attempted:
                            print("[VERIFIER] Verifier failed. Feeding back for repair...")
                            repair_attempted = True
                            history.append({"role": "user", "content": f"VERIFICATION FAILED: {v_res.model_dump_json()}\nPlease fix the issue and call finish again."})
                            step += 1
                            continue
                        elif not v_res.passed and repair_attempted:
                            print("[VERIFIER] Verifier failed again. Aborting.")
                            return v_res_dict
                        else:
                            print("[VERIFIER] Verifier passed!")
                            return v_res_dict
                print("Task finished by agent.")
                return v_res_dict
                
            step += 1
            
        await b.close()
        return None
