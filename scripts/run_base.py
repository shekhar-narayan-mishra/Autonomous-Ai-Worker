import asyncio
import os
import sqlite3
from agent.loop import run_loop

def reset_db():
    try:
        # Reset vendor portal
        conn = sqlite3.connect("mock_env/vendor_portal/data.db")
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS invoices")
        c.execute("CREATE TABLE invoices (id TEXT PRIMARY KEY, amount REAL, due_date TEXT, status TEXT)")
        c.execute("INSERT INTO invoices VALUES ('INV-100', 5000.00, '2023-11-01', 'Paid')")
        c.execute("INSERT INTO invoices VALUES ('INV-101', 1250.50, '2023-12-15', 'Unpaid')")
        conn.commit()
        conn.close()

        # Reset ERP
        conn = sqlite3.connect("mock_env/erp/erp.db")
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS erp_invoices")
        c.execute("CREATE TABLE erp_invoices (vendor TEXT, amount REAL, due_date TEXT, notes TEXT)")
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB reset error:", e)

async def main():
    reset_db()
    import mock_env.chaos as chaos
    chaos.set_chaos({k: False for k in chaos.get_chaos()})
    
    task = "Find the latest invoice from Acme Corp, extract the amount and due date, and enter it into the ERP."
    print("Running base task...")
    res = await run_loop(task, {"verifier": "invoice_entry", "vendor_name": "Acme Corp"}, max_steps=12)
    print("Result:", res)
    print("\nTrace:")
    with open("trace.jsonl", "r") as f:
        for line in f:
            print(line.strip())

if __name__ == "__main__":
    os.environ["AUTO_APPROVE"] = "true"
    asyncio.run(main())
