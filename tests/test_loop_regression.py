import pytest
import asyncio
from unittest.mock import patch, AsyncMock
from agent.loop import run_loop
from agent.llm import LLMResponse
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
            action_dict = {"thought": "Looping", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "page"}
        
        return LLMResponse(**action_dict), {"tokens": {"prompt": 10, "completion": 10}, "latency_ms": 100}

class FakeErrorLLM:
    async def generate_action(self, messages, *args, **kwargs):
        raise Exception("API rate limit exceeded")

@pytest.fixture
def mock_env(monkeypatch):
    import yaml
    env_data = {
        "apps": [
            {"name": "Vendor Portal", "base_url": "http://localhost:8001", "purpose": "View", "credentials": {"username": "admin", "password": "password123"}, "tools_to_use": ["browser"]},
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
async def test_backend_llm_error_is_error(mock_env, monkeypatch):
    seed.seed_vendor_portal("base")
    monkeypatch.setattr("agent.loop.generate_action", FakeErrorLLM().generate_action)
    
    res = await run_loop("Do a task", max_steps=5)
    assert res["status"] == "ERROR"
    assert "API rate limit exceeded" in res["report"]

@pytest.mark.asyncio
async def test_backend_loop_detection_aborts_early(mock_env, monkeypatch):
    seed.seed_vendor_portal("base")
    # Action fails repeatedly
    seq = [
        {"thought": "Try 1", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "page"},
        {"thought": "Try 2", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "page"},
        {"thought": "Try 3", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "page"},
        {"thought": "Try 4", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001"}, "expected_outcome": "page"},
    ]
    monkeypatch.setattr("agent.loop.generate_action", FakeLLM(seq).generate_action)
    
    res = await run_loop("Do a task", max_steps=30)
    # Since they are the same action without progress, no_progress_steps increments
    # and it should abort before max_steps is reached.
    assert res["status"] in ["stalled", "blocked"]
    
@pytest.mark.asyncio
async def test_backend_budget_exhaustion(mock_env, monkeypatch):
    seed.seed_vendor_portal("base")
    # Action succeeds but takes max steps
    class AlternatingLLM:
        def __init__(self):
            self.toggle = False
        async def generate_action(self, *args, **kwargs):
            self.toggle = not self.toggle
            if self.toggle:
                return LLMResponse(**{"thought": "T1", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001/1"}, "expected_outcome": "page"}), {"tokens": {}, "latency_ms": 100}
            else:
                return LLMResponse(**{"thought": "T2", "action": "browser", "args": {"command": "goto", "url": "http://localhost:8001/2"}, "expected_outcome": "page"}), {"tokens": {}, "latency_ms": 100}

    class MockBrowser:
        def __init__(self):
            self.count = 0
            self.name = "browser"
            self.description = "browser"
            self.args_schema = None
            from agent.tools.base import RiskLevel
            self.risk_level = RiskLevel.READ
        async def run(self, *args, **kwargs):
            self.count += 1
            from agent.tools.base import ToolResult
            return ToolResult(ok=True, observation=f"Page {self.count} loaded", wait_ms=0, wait_reasons=[])

    monkeypatch.setattr("agent.loop.registry.get", lambda name: MockBrowser() if name == "browser" else None)
    monkeypatch.setattr("agent.loop.generate_action", AlternatingLLM().generate_action)
    
    res = await run_loop("Do a task", max_steps=4)
    assert res["status"] == "MAX_STEPS_REACHED"
    assert "Last Obs" in res["report"]
