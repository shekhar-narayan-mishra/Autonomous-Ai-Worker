import asyncio
import os
import sqlite3
from unittest.mock import patch

from agent.llm import LLMResponse
from agent.loop import run_loop
from mock_env import chaos


# PLUMBING TEST, NOT AN AGENT EVAL
class ScriptedProvider:
    def __init__(self, sequence):
        self.sequence = sequence
        self.step = 0
        self.history = []

    async def generate_action(self, prompt: str, messages: list[dict]) -> tuple[LLMResponse, dict]:
        self.history.append({"prompt": prompt, "messages": messages})
        if self.step < len(self.sequence):
            action_dict = self.sequence[self.step]
            self.step += 1
            validated = LLMResponse.model_validate(action_dict)
            return validated, {"provider": "scripted", "model": "scripted", "tokens": {"prompt": 100, "completion": 50}, "latency_ms": 10}
        else:
            raise Exception("ScriptedProvider ran out of sequence steps")

def reset_db():
    try:
        conn = sqlite3.connect("mock_env/vendor_portal/data.db")
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS invoices")
        c.execute("CREATE TABLE invoices (id TEXT PRIMARY KEY, vendor TEXT, amount REAL, due_date TEXT, status TEXT)")
        c.execute("INSERT INTO invoices VALUES ('INV-100', 'Acme Corp', 5000.00, '2023-11-01', 'Paid')")
        c.execute("INSERT INTO invoices VALUES ('INV-101', 'Acme Corp', 1250.50, '2023-12-15', 'Unpaid')")
        c.execute("INSERT INTO invoices VALUES ('INV-AMB-1', 'Globex', 100.00, '2023-12-01', 'Paid')")
        c.execute("INSERT INTO invoices VALUES ('INV-AMB-2', 'Globex', 100.00, '2023-12-01', 'Paid')")
        conn.commit()
        conn.close()

        conn = sqlite3.connect("mock_env/erp/erp.db")
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS bills")
        c.execute("CREATE TABLE bills (id INTEGER PRIMARY KEY, invoice_id TEXT, vendor TEXT, amount REAL, due_date TEXT, notes TEXT)")
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB reset error:", e)

# 2. Base run opening INV detail page
base_sequence = [
    {"thought": "go to portal", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "portal loads"},
    {"thought": "login", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "admin"}, {"id": "e2", "value": "password123"}], "submit": "e3"}, "expected_outcome": "dashboard loads"},
    {"thought": "view invoices", "action": "browser", "args": {"command": "extract_text"}, "expected_outcome": "invoices visible"},
    {"thought": "click view INV-101", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001/invoice/INV-101"}, "expected_outcome": "invoice details"},
    {"thought": "extract detail", "action": "browser", "args": {"command": "extract_text"}, "expected_outcome": "got details"},
    {"thought": "save amount and due date", "action": "save_fact", "args": {"key": "inv_101", "value": "Amount: 1250.50, Due: 2023-12-15"}, "expected_outcome": "fact saved"},
    {"thought": "go to erp", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002"}, "expected_outcome": "erp loads"},
    {"thought": "login erp", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "admin"}, {"id": "e2", "value": "admin"}], "submit": "e3"}, "expected_outcome": "erp dashboard"},
    {"thought": "click add bill", "action": "browser", "args": {"command": "click", "selector_id": "e1"}, "expected_outcome": "add bill form"},
    {"thought": "enter invoice", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "1250.50"}, {"id": "e4", "value": "2023-12-15"}, {"id": "e5", "value": ""}], "submit": "e6"}, "expected_outcome": "submitted"},
    {"thought": "finish task", "action": "finish", "args": {"summary": "Entered invoice"}, "expected_outcome": "task complete"}
]

# a) Wrong amount entered -> verifier FAILS -> feedback -> repair -> passes
wrong_amount_sequence = [
    {"thought": "go to erp", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002"}, "expected_outcome": "erp loads"},
    {"thought": "login erp", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "admin"}, {"id": "e2", "value": "admin"}], "submit": "e3"}, "expected_outcome": "erp dashboard"},
    {"thought": "click add bill", "action": "browser", "args": {"command": "click", "selector_id": "e1"}, "expected_outcome": "add bill form"},
    {"thought": "enter WRONG amount", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "9999.99"}, {"id": "e4", "value": "2023-12-15"}, {"id": "e5", "value": ""}], "submit": "e6"}, "expected_outcome": "submitted"},
    {"thought": "finish task", "action": "finish", "args": {"summary": "Entered invoice"}, "expected_outcome": "task complete"},
    # Verifier fails, feeds back. Repair:
    {"thought": "fix amount", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002/add_bill"}, "expected_outcome": "add bill form"},
    {"thought": "re-enter CORRECT amount", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "1250.50"}, {"id": "e4", "value": "2023-12-15"}, {"id": "e5", "value": ""}], "submit": "e6"}, "expected_outcome": "submitted"},
    {"thought": "finish task", "action": "finish", "args": {"summary": "Fixed invoice"}, "expected_outcome": "task complete"}
]

# b) finish called before ERP entry -> rejected
premature_finish_sequence = [
    {"thought": "finish without doing anything", "action": "finish", "args": {"summary": "done"}, "expected_outcome": "task complete"}
]

# c) Not-found vendor -> finish reporting not found -> verifier passes
not_found_sequence = [
    {"thought": "go to portal", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "portal loads"},
    {"thought": "login", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "admin"}, {"id": "e2", "value": "password123"}], "submit": "e3"}, "expected_outcome": "dashboard loads"},
    {"thought": "extract text", "action": "browser", "args": {"command": "extract_text"}, "expected_outcome": "no unknown vendor found"},
    {"thought": "finish not found", "action": "finish", "args": {"summary": "not found"}, "expected_outcome": "missing"}
]

# d) Duplicate/ambiguity -> ask_human triggered -> answer passed back
ambiguity_sequence = [
    {"thought": "go to portal", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "portal loads"},
    {"thought": "login", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "admin"}, {"id": "e2", "value": "password123"}], "submit": "e3"}, "expected_outcome": "dashboard loads"},
    {"thought": "extract text", "action": "browser", "args": {"command": "extract_text"}, "expected_outcome": "two globex invoices"},
    {"thought": "ask human", "action": "ask_human", "args": {"question": "Which Globex invoice?"}, "expected_outcome": "INV-AMB-1"},
    {"thought": "finish after answer", "action": "finish", "args": {"summary": "Got answer"}, "expected_outcome": "task complete"}
]

async def run_sequence(name, task, task_args, seq, expect_pass):
    print(f"\n--- Running Sequence: {name} ---")
    reset_db()
    provider = ScriptedProvider(seq)
    
    with patch("agent.loop.generate_action", new=provider.generate_action):
        res = await run_loop(task, task_args, max_steps=12)
        passed = res is not None and res.get("passed", False)
        print(f"Verifier Result: {res}")
        if expect_pass and not passed:
            print(f"FAIL: Expected {name} to pass verifier.")
        elif not expect_pass and passed:
            print(f"FAIL: Expected {name} to fail verifier.")
        else:
            print(f"PASS: {name}")

async def main():
    print("=== PLUMBING TEST, NOT AN AGENT EVAL ===")
    chaos.set_chaos({k: False for k in chaos.get_chaos()})
    os.environ["AUTO_APPROVE"] = "true"
    
    # Base run
    await run_sequence("Base Task", "Find the latest invoice from Acme Corp, extract the amount and due date, and enter it into the ERP.", {"verifier": "invoice_entry", "vendor_name": "Acme Corp"}, base_sequence, True)
    
    # a) Wrong amount
    await run_sequence("Wrong Amount Repair", "Find Acme Corp", {"verifier": "invoice_entry", "vendor_name": "Acme Corp"}, wrong_amount_sequence, True)
    
    # b) Premature finish (wait, for b, premature finish means it's rejected by verifier!)
    await run_sequence("Premature Finish", "Find Acme Corp", {"verifier": "invoice_entry", "vendor_name": "Acme Corp"}, premature_finish_sequence, False)

    # c) Not-found vendor
    await run_sequence("Not Found Vendor", "Find UnknownCorp", {"verifier": "invoice_entry", "vendor_name": "UnknownCorp", "expected_outcome": "must_report_not_found"}, not_found_sequence, True)

    # d) Ambiguity
    from agent.safety import global_human_interface
    async def fake_ask(question):
        return "INV-AMB-1 is the correct one."
    global_human_interface.request_clarification = fake_ask
    
    await run_sequence("Ambiguity", "Find Globex", {"verifier": "invoice_entry", "vendor_name": "Globex", "expected_outcome": "must_ask_clarification", "expected_questions": {"globex": "INV-AMB-1"}}, ambiguity_sequence, True)

if __name__ == "__main__":
    asyncio.run(main())
