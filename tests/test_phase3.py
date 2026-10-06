import pytest
from agent.memory import MemoryStore
from agent.reliability import ReliabilityManager, ErrorClassification
from agent.safety import global_human_interface
from verifiers.invoice_entry import InvoiceEntryVerifier
import sqlite3
import os

def test_memory():
    m = MemoryStore()
    m.save_fact("foo", "bar", 1)
    prompt = m.format_for_prompt()
    assert "foo: bar" in prompt

def test_reliability():
    rm = ReliabilityManager()
    rm.record_action("a1", {"k": "v"})
    rm.record_action("a1", {"k": "v"})
    assert not rm.detect_loop()
    rm.record_action("a1", {"k": "v"})
    assert rm.detect_loop()
    
    assert rm.classify_error("timeout 500 ms") == ErrorClassification.TRANSIENT
    assert rm.classify_error("element not found") == ErrorClassification.WRONG_APPROACH

@pytest.mark.asyncio
async def test_safety():
    os.environ["AUTO_APPROVE"] = "true"
    res = await global_human_interface.request_approval("test", {}, "diff")
    assert res is True
    os.environ["AUTO_APPROVE"] = "false"

def test_verifier():
    os.makedirs("mock_env/vendor_portal", exist_ok=True)
    os.makedirs("mock_env/erp", exist_ok=True)
    pdb = sqlite3.connect("mock_env/vendor_portal/data.db")
    pdb.execute("CREATE TABLE IF NOT EXISTS invoices (id TEXT PRIMARY KEY, vendor TEXT, amount REAL, due_date TEXT, status TEXT)")
    pdb.execute("DELETE FROM invoices WHERE id = 'INV-TEST'")
    pdb.execute("INSERT INTO invoices (id, amount, due_date, vendor) VALUES ('INV-TEST', 100.0, '2024-01-01', 'Test Corp')")
    pdb.commit()
    
    edb = sqlite3.connect("mock_env/erp/erp.db")
    edb.execute("CREATE TABLE IF NOT EXISTS bills (id INTEGER PRIMARY KEY, invoice_id TEXT, amount REAL, due_date TEXT)")
    edb.execute("DELETE FROM bills")
    edb.execute("INSERT INTO bills (invoice_id, amount, due_date) VALUES ('INV-TEST', 50.0, '2024-01-01')")
    edb.commit()
    
    v = InvoiceEntryVerifier()
    res = v.verify({"vendor_name": "Test Corp"})
    assert not res.passed
    assert any(c.name == "Amount match" and not c.passed for c in res.checks)
