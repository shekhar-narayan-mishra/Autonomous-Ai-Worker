
import pytest
import yaml

from agent.llm import LLMResponse
from agent.loop import run_loop


# Mock the generate_action to just return our ask_human call, then a finish call
class MockLLM:
    def __init__(self):
        self.step = 0
        
    async def generate_action(self, prompt, messages):
        self.step += 1
        if self.step == 1:
            return LLMResponse(
                thought="I need credentials",
                action="ask_human",
                args={"question": "What is the login password for internal erp?"},
                expected_outcome="get password"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}
        else:
            return LLMResponse(
                thought="finish",
                action="finish",
                args={"summary": "done", "evidence": "url"},
                expected_outcome="done"
            ), {"provider": "mock", "tokens": {}, "latency_ms": 0}

@pytest.mark.asyncio
async def test_ask_human_bypass_credentials(monkeypatch):
    mock = MockLLM()
    monkeypatch.setattr("agent.loop.generate_action", mock.generate_action)
    
    # ensure environment.yaml exists for test by mocking
    env_data = {
        "apps": [
            {"name": "Internal ERP", "base_url": "http://localhost:8002", "credentials": {"username": "admin"}}
        ]
    }
    
    from unittest.mock import mock_open
    m_open = mock_open(read_data=yaml.dump(env_data))
    monkeypatch.setattr("builtins.open", m_open)
        
    # We also need to intercept trace logger to see the result
    trace_events = []
    class MockTrace:
        def __init__(self, *args, **kwargs): pass
        def log_step(self, step, thought, action, args, obs, ok, tokens, latency, shot_path=None, attempts=None): 
            trace_events.append({"action": action, "observation": obs})
    monkeypatch.setattr("agent.loop.TraceLogger", MockTrace)
    
    await run_loop("Do something")
    
    # Check trace events
    ask_event = next((e for e in trace_events if e["action"] == "ask_human"), None)
    assert ask_event is not None
    assert "Credentials are in Available environment: Internal ERP" in ask_event["observation"]
