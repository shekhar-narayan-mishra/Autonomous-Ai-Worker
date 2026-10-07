import asyncio
import json
import sqlite3
import sys

from agent.loop import run_loop


async def main():
    if len(sys.argv) < 2:
        print("Usage: python -m agent.run \"<task>\" [task_args_json]")
        sys.exit(1)
    task = sys.argv[1]
    task_args = {}
    if len(sys.argv) > 2:
        task_args = json.loads(sys.argv[2])
        if "expected_questions" in task_args:
            from agent.safety import global_human_interface
            global_human_interface.set_expected(task_args["expected_questions"])
        
    print(f"Running task: {task}")
    await run_loop(task, task_args)
    
    conn = sqlite3.connect("mock_env/erp/erp.db")
    conn.row_factory = sqlite3.Row
    bills = conn.execute("SELECT * FROM bills").fetchall()
    print("\nFinal ERP Bills:")
    for b in bills:
        print(dict(b))
        
    from agent.llm import get_run_metrics
    metrics = get_run_metrics()
    metrics.finalize()
    print(f"\n[Run Metrics] Status: {metrics.status} | Providers: {metrics.providers_used} | Models: {metrics.models_used} | Switches: {metrics.model_switches} | 429s: {metrics.rate_limit_429_count} | Calls: {metrics.successful_calls} | Prompt Tokens: {metrics.total_prompt_tokens} | Comp Tokens: {metrics.total_completion_tokens} | Latency: {metrics.total_latency_ms}ms")

if __name__ == "__main__":
    asyncio.run(main())
