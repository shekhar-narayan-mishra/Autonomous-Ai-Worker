import pytest
import os
import yaml
from unittest.mock import patch, mock_open

from agent.llm import LLMResponse
from agent.loop import run_loop
import agent.tools.finish  # Ensure finish tool is registered
import agent.memory  # Ensure memory tools are registered
from agent.verifier import VerifierResult, CheckResult

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

    # Need max_steps to trigger abort eventually because it fails and loops
    await run_loop("Do something", max_steps=1)
    
    # Check trace events
    finish_event = next((e for e in trace_events if e["action"] == "finish"), None)
    assert finish_event is not None
    assert "finish requires an 'evidence' field" in finish_event["observation"]

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
