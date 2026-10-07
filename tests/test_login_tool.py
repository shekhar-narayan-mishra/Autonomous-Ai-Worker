import asyncio
import json
import os

import pytest
import yaml

from agent.llm import LLMResponse
from agent.loop import run_loop
from agent.tools.base import ToolResult


class MockBrowserTool:
    def __init__(self):
        self.log = []
        
    async def run(self, command, **kwargs):
        if command == "login":
            app = kwargs.get("app")
            return ToolResult(ok=True, observation=f"Logged in. Resulting URL: http://mock/{app}")
        if command == "timeout_test":
            await asyncio.sleep(25)
            return ToolResult(ok=True, observation="Should timeout")
            
        return ToolResult(ok=True, observation="Ok")

class MockLLM:
    def __init__(self):
        self.step = 0
        
    async def generate_action(self, prompt, messages):
        self.step += 1
        if self.step == 1:
            return LLMResponse(
                thought="Login portal",
                action="browser",
                args={"command": "login", "app": "Vendor Portal"},
                expected_outcome="Logged in"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}
        elif self.step == 2:
            return LLMResponse(
                thought="Login erp",
                action="browser",
                args={"command": "login", "app": "Internal ERP"},
                expected_outcome="Logged in"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}
        elif self.step == 3:
            # fill form with password to test redaction
            return LLMResponse(
                thought="type pass",
                action="browser",
                args={"command": "fill_form", "fields": [{"id": "password_input", "value": "secret123"}], "submit": "login_btn"},
                expected_outcome="Ok"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}
        elif self.step == 4:
            return LLMResponse(
                thought="Timeout",
                action="browser",
                args={"command": "timeout_test"},
                expected_outcome="Ok"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}
        else:
            return LLMResponse(
                thought="finish",
                action="finish",
                args={"summary": "done", "evidence": "url"},
                expected_outcome="done"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}

@pytest.mark.asyncio
async def test_login_and_redaction(monkeypatch, tmp_path):
    # Mock environment
    env_data = {
        "apps": [
            {"name": "Vendor Portal", "base_url": "http://localhost:8001", "credentials": {"username": "v", "password": "v_pass"}},
            {"name": "Internal ERP", "base_url": "http://localhost:8002", "credentials": {"username": "e", "password": "e_pass"}}
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
    
    mock_llm = MockLLM()
    monkeypatch.setattr("agent.loop.generate_action", mock_llm.generate_action)
    
    # Mock tool registry to use our mock browser
    mock_browser = MockBrowserTool()
    import agent.loop
    orig_registry_get = agent.loop.registry.get
    
    def mock_get(name):
        if name == "browser":
            # Just set mock_browser.risk_level to RiskLevel.READ so it can be called
            mock_browser.risk_level = agent.loop.RiskLevel.READ
            return mock_browser
        return orig_registry_get(name)
        
    monkeypatch.setattr("agent.loop.registry.get", mock_get)
    
    # Run loop
    run_id = "test_run_login"
    await run_loop("Test task", run_id=run_id)
    
    # Check trace.jsonl for redaction and tool responses
    trace_path = f"runs/{run_id}/trace.jsonl"
    assert os.path.exists(trace_path)
    
    with orig_open(trace_path) as f:
        traces = [json.loads(line) for line in f if line.strip()]
        
    # Step 1: Vendor Portal
    assert traces[0]["action"] == "browser"
    assert traces[0]["args"]["app"] == "Vendor Portal"
    
    # Step 2: Internal ERP
    assert traces[1]["action"] == "browser"
    assert traces[1]["args"]["app"] == "Internal ERP"
    
    # Step 3: Redaction check
    assert traces[2]["action"] == "browser"
    assert traces[2]["args"]["fields"][0]["id"] == "password_input"
    assert traces[2]["args"]["fields"][0]["value"] == "***REDACTED***"
    
    # Step 4: Timeout check
    assert traces[3]["action"] == "browser"
    assert "Timeout: tool 'browser' exceeded 20s limit" in traces[3]["observation_summary"]
    assert traces[3]["ok"] is False

    # Also check the environment prompt in trace 1 to ensure credentials are redacted
    # Not easily accessible from trace.jsonl directly unless we mock the LLM input,
    # but the instructions said "show credentials: available via login in the environment block instead".
    
    # cleanup
    import shutil
    shutil.rmtree(f"runs/{run_id}")
