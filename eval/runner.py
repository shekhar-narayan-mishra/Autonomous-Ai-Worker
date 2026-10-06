import yaml
import subprocess
import os
import json
import time
import glob
import random
from statistics import mean

CHAOS_FLAGS = [
    "slow_load",
    "random_popup_modal",
    "expired_session",
    "validation_error_on_first_submit",
    "missing_field",
    "flaky_500",
    "flaky_500_erp"
]

def analyze_trace(log_path):
    steps = 0
    tokens = 0
    latency = 0
    retries = 0
    unexpected_questions = 0
    rate_limits = 0
    if not os.path.exists(log_path):
        return steps, tokens, latency, retries, unexpected_questions, rate_limits
        
    with open(log_path, "r") as f:
        for line in f:
            if not line.strip(): continue
            try:
                data = json.loads(line)
                steps += 1
                tokens += data.get("tokens", 0)
                latency += data.get("latency_ms", 0)
                if "retry" in data.get("action", "") or "Retry" in data.get("thought", ""):
                    retries += 1
                # If there's a 429 mentioned in obs
                if "429" in str(data.get("observation", "")):
                    rate_limits += 1
                if "FAILED: Unexpected question" in str(data.get("observation", "")):
                    unexpected_questions += 1
            except:
                pass
    return steps, tokens, latency, retries, unexpected_questions, rate_limits

def run_eval():
    os.environ["EVAL_MODE"] = "1"
    os.environ["AUTO_APPROVE"] = "true"
    
    with open("eval/tasks.yaml") as f:
        tasks = yaml.safe_load(f)
        
    results = []
    
    # Start portals
    vp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.vendor_portal.main:app", "--port", "8001"])
    erp_proc = subprocess.Popen(["venv/bin/uvicorn", "mock_env.erp.main:app", "--port", "8002"])
    time.sleep(2)
    
    try:
        for t in tasks:
            print(f"\nEvaluating Task: {t['id']}", flush=True)
            task_res = {
                "id": t["id"],
                "runs": [],
                "expected": t["expected_outcome"]
            }
            
            for is_chaos in [False, False, False, True, True, True]:
                subprocess.run(["venv/bin/python", "mock_env/seed.py"], check=True)
                
                if is_chaos:
                    flag = random.choice(CHAOS_FLAGS)
                    with open("chaos_state.json", "w") as f:
                        json.dump({flag: True}, f)
                else:
                    if os.path.exists("chaos_state.json"):
                        os.remove("chaos_state.json")
                        
                args = {
                    "verifier": t.get("verifier", "invoice_entry"),
                    "vendor_name": t.get("vendor_name", "Acme Corp")
                }
                if "expected_questions" in t:
                    args["expected_questions"] = t["expected_questions"]
                    
                # Run agent
                run_cmd = ["venv/bin/python", "-m", "agent.run", t["task"], json.dumps(args)]
                start_t = time.time()
                proc = subprocess.run(run_cmd, capture_output=True, text=True)
                end_t = time.time()
                
                # Check outcome
                passed_verifier = "[VERIFIER] Verifier passed!" in proc.stdout
                failed_verifier = "[VERIFIER] Verifier failed" in proc.stdout
                sys_exit = proc.returncode != 0
                not_found = "not found" in proc.stdout.lower()
                clarification = "[AGENT ASKS]" in proc.stdout
                
                outcome = "unknown"
                if t["expected_outcome"] == "success":
                    if passed_verifier: outcome = "success"
                    else: outcome = "failed"
                elif t["expected_outcome"] == "must_ask_clarification":
                    if clarification and not sys_exit: outcome = "success"
                    else: outcome = "failed"
                elif t["expected_outcome"] == "must_report_not_found":
                    if not_found: outcome = "success"
                    else: outcome = "failed"
                    
                # Parse trace
                steps, tokens, latency, retries, unexpected_questions, rate_limits = analyze_trace("trace.jsonl")
                
                run_data = {
                    "chaos": is_chaos,
                    "outcome": outcome,
                    "passed_verifier": passed_verifier,
                    "sys_exit": sys_exit,
                    "steps": steps,
                    "tokens": tokens,
                    "latency": latency,
                    "retries": retries,
                    "unexpected_q": unexpected_questions,
                    "rate_limits": rate_limits,
                    "time": end_t - start_t,
                    "stdout": proc.stdout
                }
                task_res["runs"].append(run_data)
                print(f"  Run (Chaos={is_chaos}): {outcome}", flush=True)
                
            results.append(task_res)
            
    finally:
        vp_proc.terminate()
        erp_proc.terminate()
        
    # Write results.md
    with open("eval/results.md", "w") as f:
        f.write("# Eval Results\n\n")
        f.write(f"**Model:** {os.getenv('MODEL_NAME', 'llama-3.1-8b-instant')}\n\n")
        
        f.write("| Task ID | Success Rate | Chaos Recovery | Verifier Pass | Avg Steps | Avg Retries | Avg Tokens | Avg Latency (ms) | Unexp Q's | 429s |\n")
        f.write("|---------|--------------|----------------|---------------|-----------|-------------|------------|-----------------|-----------|------|\n")
        
        failures = []
        
        for t in results:
            runs = t["runs"]
            successes = sum(1 for r in runs if r["outcome"] == "success")
            chaos_runs = [r for r in runs if r["chaos"]]
            chaos_success = sum(1 for r in chaos_runs if r["outcome"] == "success")
            verif_pass = sum(1 for r in runs if r["passed_verifier"])
            avg_steps = mean([r["steps"] for r in runs]) if runs else 0
            avg_retries = mean([r["retries"] for r in runs]) if runs else 0
            avg_tokens = mean([r["tokens"] for r in runs]) if runs else 0
            avg_lat = mean([r["latency"] for r in runs]) if runs else 0
            unexp_q = sum(r["unexpected_q"] for r in runs)
            limits = sum(r["rate_limits"] for r in runs)
            
            sr = f"{(successes/len(runs))*100:.0f}%" if runs else "0%"
            cr = f"{(chaos_success/len(chaos_runs))*100:.0f}%" if chaos_runs else "N/A"
            vp = f"{(verif_pass/len(runs))*100:.0f}%" if runs else "0%"
            
            f.write(f"| {t['id']} | {sr} | {cr} | {vp} | {avg_steps:.1f} | {avg_retries:.1f} | {avg_tokens:.0f} | {avg_lat:.0f} | {unexp_q} | {limits} |\n")
            
            for i, r in enumerate(runs):
                if r["outcome"] != "success":
                    failures.append(f"- **{t['id']}** (Run {i+1}, Chaos={r['chaos']}): Expected {t['expected']}, got {r['outcome']}. Details: SysExit={r['sys_exit']}, Verifier={r['passed_verifier']}")
                    
        f.write("\n## Failures\n")
        if failures:
            for fail in failures:
                f.write(f"{fail}\n")
        else:
            f.write("No failures!\n")

if __name__ == "__main__":
    run_eval()
