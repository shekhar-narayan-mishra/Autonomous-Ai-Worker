
import pytest

from agent.llm import LLMResponse
from agent.loop import run_loop
from mock_env import seed


class FakeLLM:
    def __init__(self, sequence):
        self.sequence = sequence
        self.step_idx = 0

    async def generate_action(self, messages, *args, **kwargs):
        if self.step_idx < len(self.sequence):
            action_dict = self.sequence[self.step_idx]
            self.step_idx += 1
        else:
            action_dict = {"thought": "Out of steps", "action": "finish", "args": {"summary": "done", "evidence": ""}, "expected_outcome": "done"}
        
        return LLMResponse(**action_dict), {"tokens": {"prompt": 10, "completion": 10}, "latency_ms": 100}

@pytest.fixture
def mock_env(monkeypatch):
    import yaml
    env_data = {
        "apps": [
            {"name": "Vendor Portal", "base_url": "http://localhost:8001", "purpose": "View and search vendor invoices", "credentials": {"username": "admin", "password": "password123"}, "tools_to_use": ["browser"]},
            {"name": "Internal ERP", "base_url": "http://localhost:8002", "purpose": "Data entry", "credentials": {"username": "admin", "password": "admin"}, "tools_to_use": ["browser"]}
        ]
    }
    import builtins
    orig_open = builtins.open
    def mock_open(path, *args, **kwargs):
        if "environment.yaml" in str(path):
            from io import StringIO
            return StringIO(yaml.dump(env_data))
        return orig_open(path, *args, **kwargs)
    monkeypatch.setattr("builtins.open", mock_open)

@pytest.mark.asyncio
async def test_full_scripted_e2e_pass(mock_env, monkeypatch, capsys):
    print("\\n[plumbing test, not agent eval] E2E Pass")
    seed.seed_vendor_portal("base")
    seed.seed_erp("base") # Ensure DB is seeded properly
    import agent.loop
    
    seq = [
        {"thought": "goto portal", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "page"},
        {"thought": "login portal", "action": "browser", "args": {"command": "login", "app": "Vendor Portal"}, "expected_outcome": "in"},
        {"thought": "goto inv", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001/invoice/INV-101"}, "expected_outcome": "inv"},
        {"thought": "save fact", "action": "save_fact", "args": {"key": "amt", "value": "1200.5"}, "expected_outcome": "saved"},
        {"thought": "goto erp", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002"}, "expected_outcome": "page"},
        {"thought": "login erp", "action": "browser", "args": {"command": "login", "app": "Internal ERP"}, "expected_outcome": "in"},
        {"thought": "goto bill", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002/add_bill"}, "expected_outcome": "bill"},
        {"thought": "fill", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "1200.5"}, {"id": "e4", "value": "2024-11-15"}], "submit": "e6"}, "expected_outcome": "added"},
        {"thought": "finish", "action": "finish", "args": {"summary": "done", "evidence": "did it"}, "expected_outcome": "done"}
    ]
    
    fake_llm = FakeLLM(seq)
    monkeypatch.setattr(agent.loop, "generate_action", fake_llm.generate_action)
    
    res = await run_loop("Enter invoice INV-101 from Vendor Portal into Internal ERP", task_args={"verifier": "invoice_entry", "vendor_name": "Acme Corp", "invoice_id": "INV-101"})
    
    assert res["status"] == "SUCCESS", f"Expected SUCCESS, got {res.get('status')}"
    
    # Print trace table
    print("Step | Action | OK | Tool MS")
    for step in res.get("steps", []):
        print(f"{step.get('step', '-')} | {step.get('action')} | {step.get('ok')} | {step.get('tool_ms', 0)}")

@pytest.mark.asyncio
async def test_full_scripted_e2e_wrong_amount_repair(mock_env, monkeypatch, capsys):
    print("\\n[plumbing test, not agent eval] E2E Wrong Amount -> Repair")
    seed.seed_vendor_portal("base")
    seed.seed_erp("base")
    import agent.loop
    
    seq = [
        {"thought": "goto portal", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "page"},
        {"thought": "login portal", "action": "browser", "args": {"command": "login", "app": "Vendor Portal"}, "expected_outcome": "in"},
        {"thought": "goto inv", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001/invoice/INV-101"}, "expected_outcome": "inv"},
        {"thought": "goto erp", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002"}, "expected_outcome": "page"},
        {"thought": "login erp", "action": "browser", "args": {"command": "login", "app": "Internal ERP"}, "expected_outcome": "in"},
        {"thought": "goto bill", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002/add_bill"}, "expected_outcome": "bill"},
        # WRONG AMOUNT: 999.9 instead of 1200.5
        {"thought": "fill", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "999.9"}, {"id": "e4", "value": "2024-11-15"}], "submit": "e6"}, "expected_outcome": "added"},
        {"thought": "finish", "action": "finish", "args": {"summary": "done", "evidence": "did it"}, "expected_outcome": "done"},
        
        # Repair steps (triggered after verifier fails)
        {"thought": "repair goto bill", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002/add_bill"}, "expected_outcome": "bill"},
        {"thought": "repair fill", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "1200.5"}, {"id": "e4", "value": "2024-11-15"}], "submit": "e6"}, "expected_outcome": "added"},
        {"thought": "finish again", "action": "finish", "args": {"summary": "fixed", "evidence": "repaired"}, "expected_outcome": "done"}
    ]
    
    fake_llm = FakeLLM(seq)
    
    async def mock_generate_action(*args, **kwargs):
        if fake_llm.step_idx == 8:
            import sqlite3
            with sqlite3.connect("mock_env/erp/erp.db") as conn:
                conn.execute("DELETE FROM bills WHERE amount=999.9")
        return await fake_llm.generate_action(*args, **kwargs)
        
    monkeypatch.setattr(agent.loop, "generate_action", mock_generate_action)
    
    res = await run_loop("Enter invoice INV-101 from Vendor Portal into Internal ERP", task_args={"verifier": "invoice_entry", "vendor_name": "Acme Corp", "invoice_id": "INV-101"})
    
    assert res["status"] == "SUCCESS_AFTER_REPAIR", f"Expected SUCCESS_AFTER_REPAIR after repair, got {res.get('status')}"

@pytest.mark.asyncio
async def test_full_scripted_e2e_skip_portal(mock_env, monkeypatch, capsys):
    print("\\n[plumbing test, not agent eval] E2E Skip Portal")
    seed.seed_vendor_portal("base")
    seed.seed_erp("base")
    import agent.loop
    
    seq = [
        {"thought": "goto erp", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002"}, "expected_outcome": "page"},
        {"thought": "skip portal, just login erp", "action": "browser", "args": {"command": "login", "app": "Internal ERP"}, "expected_outcome": "in"},
        {"thought": "goto bill", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8002/add_bill"}, "expected_outcome": "bill"},
        # Good values, but we never visited Vendor Portal, so let's say the agent guessed wrong
        {"thought": "fill", "action": "browser", "args": {"command": "fill_form", "fields": [{"id": "e1", "value": "INV-101"}, {"id": "e2", "value": "Acme Corp"}, {"id": "e3", "value": "99.99"}, {"id": "e4", "value": "2024-11-15"}], "submit": "e6"}, "expected_outcome": "added"},
        {"thought": "finish", "action": "finish", "args": {"summary": "done", "evidence": "did it"}, "expected_outcome": "done"},
        
        # Repair attempt: We won't fix it properly, just finish again
        {"thought": "finish again", "action": "finish", "args": {"summary": "no fix", "evidence": "no"}, "expected_outcome": "done"}
    ]
    
    fake_llm = FakeLLM(seq)
    monkeypatch.setattr(agent.loop, "generate_action", fake_llm.generate_action)
    
    res = await run_loop("Enter invoice INV-101", task_args={"verifier": "invoice_entry", "vendor_name": "Acme Corp", "invoice_id": "INV-101"})
    
    assert res["status"] == "FAILED_VERIFICATION", f"Expected FAILED_VERIFICATION, got {res.get('status')}"
