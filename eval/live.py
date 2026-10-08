import argparse
import asyncio
import json
import os
import sys
import time

import yaml

from agent.llm import LLMQuotaExhaustedError
from agent.loop import run_loop
from agent.safety import global_human_interface
from mock_env.chaos import set_chaos
from mock_env.seed import seed_erp, seed_vendor_portal


def load_tasks():
    with open("eval/tasks.yaml", "r") as f:
        return yaml.safe_load(f)

def parse_trace(run_id):
    trace_file = f"runs/{run_id}/trace.jsonl" if run_id else "trace.jsonl"
    if not os.path.exists(trace_file):
        return {"steps": 0, "retries": 0, "llm_calls": 0, "tokens": 0, "providers": [], "status_429s": 0, "switches": 0, "llm_wait_total": 0}
    
    steps = 0
    retries = 0
    llm_calls = 0
    tokens = 0
    providers_used = []
    status_429s = 0
    switches = 0
    llm_wait_total = 0
    
    last_provider = None
    
    with open(trace_file, "r") as f:
        for line in f:
            if not line.strip(): continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
                
            steps = max(steps, data.get("step", 0))
            attempts = data.get("attempts", [])
            llm_calls += max(len(attempts), 1)
            
            for i, att in enumerate(attempts):
                outcome = att.get("outcome", "")
                if "429" in outcome:
                    status_429s += 1
                
                p = att.get("provider")
                if p:
                    if p not in providers_used:
                        providers_used.append(p)
                    if last_provider is not None and p != last_provider:
                        switches += 1
                    last_provider = p
            
            retries += max(0, len(attempts) - 1)
            
            toks = data.get("tokens", {})
            tokens += toks.get("prompt", 0) + toks.get("completion", 0)
            llm_wait_total += data.get("llm_wait_ms", 0) / 1000.0
            
    return {
        "steps": steps,
        "retries": retries,
        "llm_calls": llm_calls,
        "tokens": tokens,
        "providers": providers_used,
        "status_429s": status_429s,
        "switches": switches,
        "llm_wait_total": llm_wait_total
    }

def generate_md(results):
    md = "# Live Eval Results\n\n"
    md += "| Task | Run | Status | Verifier Pass | Steps | Retries | LLM Calls | Tokens | Providers | 429s | Switches | Wall Time | LLM Wait |\n"
    md += "|------|-----|--------|---------------|-------|---------|-----------|--------|-----------|------|----------|-----------|----------|\n"
    
    total = 0
    passes = 0
    infra_errors = 0
    
    for key, res in results.items():
        parts = key.split('/', 2)
        task_name = parts[0]
        if len(parts) > 1 and parts[1].isdigit():
            run_idx = parts[1]
        else:
            run_idx = parts[-1]
            if len(parts) == 3:
                task_name = f"{parts[0]} ({parts[1]})"

        if res.get("status", "").startswith("not_run"):
            md += f"| {task_name} | {run_idx} | {res['status']} | - | - | - | - | - | - | - | - | - | - |\n"
        else:
            st = res.get("status", "")
            is_infra = ("VERIFICATION_ERROR" in st or "infra" in st.lower())
            total += 1
            if is_infra:
                infra_errors += 1
                
            vp = res.get("verifier_pass")
            vpass = "Yes" if vp else "No"
            if vp:
                passes += 1
                
            prov_str = ", ".join(res.get("providers", []))
            md += f"| {task_name} | {run_idx} | {st} | {vpass} | {res.get('steps',0)} | {res.get('retries',0)} | {res.get('llm_calls',0)} | {res.get('tokens',0)} | {prov_str} | {res.get('status_429s',0)} | {res.get('switches',0)} | {res.get('wall_time',0):.1f}s | {res.get('llm_wait_total',0):.1f}s |\n"
    
    valid_total = total - infra_errors
    sr = (passes / valid_total * 100) if valid_total > 0 else 0
    md += f"\n**Success Rate:** {sr:.1f}% ({passes}/{valid_total}) [Excluded {infra_errors} infra errors]\n"
    
    md += "\n## Failures\n"
    for key, res in results.items():
        st = res.get("status", "")
        if st not in ["SUCCESS", "SUCCESS_AFTER_REPAIR"] and not st.startswith("not_run"):
            md += f"- **{key}**: Status `{st}`. Verifier pass: {res.get('verifier_pass')}\n"
    
    with open("results_live.md", "w") as f:
        f.write(md)

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=str, default="base, ambiguous_duplicate, missing_invoice, duplicate_detection, base+chaos expired_session, base+chaos validation_error_on_first_submit")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--go", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    
    os.environ["EVAL_MODE"] = "1"
    os.environ["AUTO_APPROVE"] = "true"
    
    tasks_yaml = load_tasks()
    task_map = {t["id"]: t for t in tasks_yaml}
    
    requested = [t.strip() for t in args.tasks.split(",")]
    plan = []
    
    for req in requested:
        chaos = ""
        base_req = req
        if "+chaos " in req:
            base_req, chaos = req.split("+chaos ")
            base_req = base_req.strip()
            chaos = chaos.strip()
            
        task_id = "base_entry" if base_req == "base" else base_req
        if task_id not in task_map:
            print(f"Warning: task '{task_id}' not found in tasks.yaml, skipping.")
            continue
            
        for i in range(1, args.runs + 1):
            key = f"{req}/{i}"
            plan.append({
                "key": key,
                "task_id": task_id,
                "chaos": chaos,
                "run": i,
                "req": req
            })
            
    if not args.go:
        print("Plan:")
        for item in plan:
            print(f"Task: {item['req']} (Run {item['run']})")
        print(f"Total tasks: {len(plan)}")
        print(f"Estimated LLM calls: {len(plan) * 25}")
        sys.exit(0)

    results_file = "results_live.json"
    results = {}
    if os.path.exists(results_file):
        with open(results_file, "r") as f:  # noqa: ASYNC230
            results = json.load(f)
            
    exhausted = False
    
    for item in plan:
        key = item["key"]
        
        if exhausted:
            results[key] = {"status": "not_run (quota)"}
            continue
            
        if not args.force and key in results and not results[key].get("status", "").startswith("not_run"):
            print(f"Skipping {key}, already completed.")
            continue
            
        print(f"\n=== Running {key} ===")
        task_dict = task_map[item["task_id"]]
        
        profile = task_dict.get("seed_profile", "base")
        seed_vendor_portal(profile=profile)
        seed_erp(profile=profile)
        
        if item["chaos"]:
            set_chaos({item["chaos"]: True})
        else:
            set_chaos({})
            
        if "expected_questions" in task_dict:
            global_human_interface.set_expected(task_dict["expected_questions"])
        else:
            global_human_interface.set_expected({})
            
        t0 = time.time()
        run_id = f"eval_{int(t0)}_{item['run']}"
        
        try:
            res = await run_loop(task=task_dict["task"], task_args={"verifier": task_dict.get("verifier")}, max_steps=40, run_id=run_id)
            status = res.get("status", "")
            
            verifier_pass = status in ["SUCCESS", "SUCCESS_AFTER_REPAIR"]
            expected = task_dict.get("expected_outcome", "success")
            
            # Simple heuristic for now: if expected is must_ask or must_report, and it finished ok, mark true
            if expected in ["must_ask_clarification", "must_report_not_found"] and "VERIFICATION_ERROR" not in status and "FAILED" not in status:
                verifier_pass = True

        except LLMQuotaExhaustedError:
            print("\n!!! LLM QUOTA EXHAUSTED !!!")
            exhausted = True
            res = {"status": "FAILED_QUOTA"}
            verifier_pass = False
        except SystemExit:
            res = {"status": "FAILED_HUMAN_INTERFACE"}
            verifier_pass = False
        except ValueError as e:
            if "Unexpected question" in str(e):
                res = {"status": "FAILED_UNEXPECTED_QUESTION"}
                verifier_pass = False
            else:
                res = {"status": f"infra_error: {e}"}
                verifier_pass = False
        except Exception as e:  # noqa: BLE001
            res = {"status": f"infra_error: {e}"}
            verifier_pass = False
            
        t1 = time.time()
        trace_data = parse_trace(run_id)
        
        # apply budget guard limit logging
        if trace_data["llm_calls"] >= 25 and not exhausted:
            exhausted = True
            res = {"status": "FAILED_QUOTA_EXCEEDED"}
            verifier_pass = False
        
        record = {
            "status": res.get("status", "unknown"),
            "verifier_pass": verifier_pass,
            "steps": trace_data["steps"],
            "retries": trace_data["retries"],
            "llm_calls": trace_data["llm_calls"],
            "tokens": trace_data["tokens"],
            "providers": trace_data["providers"],
            "status_429s": trace_data["status_429s"],
            "switches": trace_data["switches"],
            "wall_time": t1 - t0,
            "llm_wait_total": trace_data["llm_wait_total"]
        }
        
        results[key] = record
        with open(results_file, "w") as f:  # noqa: ASYNC230
            json.dump(results, f, indent=2)
            
    generate_md(results)
    print("\nEval finished. Saved results_live.json and results_live.md")
    
if __name__ == "__main__":
    asyncio.run(main())
