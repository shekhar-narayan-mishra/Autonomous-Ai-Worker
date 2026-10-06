import sys
import asyncio
from agent.loop import run_loop
import sqlite3
import json

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

if __name__ == "__main__":
    asyncio.run(main())
