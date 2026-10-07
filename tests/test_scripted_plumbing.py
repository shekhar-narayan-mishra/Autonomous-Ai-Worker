import os

import pytest

import agent.memory  # noqa: F401
import agent.tools.finish  # noqa: F401
from agent.llm import LLMResponse
from agent.loop import run_loop
from agent.verifier import VerifierResult


class MockStallingLLM:
    async def generate_action(self, prompt, messages):
        return LLMResponse(
            thought="Saving a fact over and over",
            action="save_fact",
            args={"key": "test", "value": "loop"},
            expected_outcome="Saved"
        ), {"provider": "mock", "tokens": {}, "latency_ms": 0}

@pytest.mark.asyncio
async def test_stalling_abort(monkeypatch):
    mock = MockStallingLLM()
    monkeypatch.setattr("agent.loop.generate_action", mock.generate_action)
    
    trace_events = []
    class MockTrace:
        def __init__(self, *args, **kwargs): pass
        def log_step(self, step, thought, action, args, obs, ok, tokens, latency, shot_path=None, attempts=None): 
            trace_events.append({"action": action, "observation": obs})
    monkeypatch.setattr("agent.loop.TraceLogger", MockTrace)

    res = await run_loop("Do something")
    assert res["status"] == "stalled"
    assert "Abort: Stalled." in res["report"]

class MockEmptyEvidenceLLM:
    async def generate_action(self, prompt, messages):
        return LLMResponse(
            thought="I am finished",
            action="finish",
            args={"summary": "done", "evidence": ""},
            expected_outcome="done"
        ), {"provider": "mock", "tokens": {}, "latency_ms": 0}

@pytest.mark.asyncio
async def test_empty_evidence_reject(monkeypatch):
    mock = MockEmptyEvidenceLLM()
    monkeypatch.setattr("agent.loop.generate_action", mock.generate_action)
    
    trace_events = []
    class MockTrace:
        def __init__(self, *args, **kwargs): pass
        def log_step(self, step, thought, action, args, obs, ok, tokens, latency, shot_path=None, attempts=None): 
            trace_events.append({"action": action, "observation": obs})
    monkeypatch.setattr("agent.loop.TraceLogger", MockTrace)

    monkeypatch.setattr("agent.loop.TraceLogger", MockTrace)

    async def mock_ask(self, msg): return "Mock response"
    monkeypatch.setattr("agent.safety.HumanInterface.ask", mock_ask)

    # Run loop, which will stall
    res = await run_loop("Do something")
    assert res["status"] in ["stalled", "blocked"]
    assert "Abort: Stalled" in res["report"]

class MockRepairLLM:
    def __init__(self):
        self.step = 0
    async def generate_action(self, prompt, messages):
        self.step += 1
        if self.step == 1:
            return LLMResponse(
                thought="Skipping portal, guessing value",
                action="finish",
                args={"summary": "done", "evidence": "guessed"},
                expected_outcome="done"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}
        else:
            return LLMResponse(
                thought="Repairing",
                action="finish",
                args={"summary": "done", "evidence": "fixed"},
                expected_outcome="done"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}

@pytest.mark.asyncio
async def test_repair_flow(monkeypatch):
    mock = MockRepairLLM()
    monkeypatch.setattr("agent.loop.generate_action", mock.generate_action)
    
    class FakeVerifier:
        def __init__(self):
            self.calls = 0
        def verify(self, args):
            self.calls += 1
            if self.calls == 1:
                return VerifierResult(passed=False, checks=[], evidence={})
            return VerifierResult(passed=True, checks=[], evidence={})
            
    fake_verifier = FakeVerifier()
    monkeypatch.setattr("agent.loop.get_verifier", lambda x: fake_verifier)

    res = await run_loop("Do something", task_args={"verifier": "dummy"})
    assert res["status"] == "SUCCESS_AFTER_REPAIR"

class MockAskHumanPauseLLM:
    def __init__(self):
        self.step = 0
    async def generate_action(self, prompt, messages):
        self.step += 1
        if self.step == 1:
            return LLMResponse(
                thought="I need to ask human",
                action="ask_human",
                args={"question": "What is the vendor name?"},
                expected_outcome="get vendor name"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}
        else:
            return LLMResponse(
                thought="I am finished",
                action="finish",
                args={"summary": "done", "evidence": "vendor name is Test Vendor"},
                expected_outcome="done"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}

@pytest.mark.asyncio
async def test_ask_human_pause_and_continue(monkeypatch):
    mock = MockAskHumanPauseLLM()
    monkeypatch.setattr("agent.loop.generate_action", mock.generate_action)
    
    from agent.safety import global_human_interface
    
    global_human_interface.set_expected({"What is the vendor name?": "Test Vendor"})
    monkeypatch.setenv("EVAL_MODE", "1")
    
    # Need dummy verifier
    class FakeVerifier:
        def verify(self, args):
            from agent.verifier import VerifierResult
            return VerifierResult(passed=True, checks=[], evidence={})
    monkeypatch.setattr("agent.verifier._REGISTRY", {"dummy": FakeVerifier})
    
    res = await run_loop("Find vendor name", task_args={"verifier": "dummy"})
    assert res["status"] == "SUCCESS"

def test_ask_human_in_schema():
    from agent.llm import get_dynamic_response_schema
    from agent.tools.base import registry
    schema = get_dynamic_response_schema(registry)
    schema_json = schema.model_json_schema()
    enum_vals = schema_json['properties']['action']['enum']
    assert "ask_human" in enum_vals
