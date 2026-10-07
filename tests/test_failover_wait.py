import time
from unittest.mock import patch

import pytest

from agent.llm import (
    LLMChainManager,
    LLMResponse,
    generate_action,
    reset_run_metrics,
    set_chain_manager,
)


@pytest.mark.asyncio
async def test_failover_on_long_per_minute_wait(tmp_path):
    test_state = str(tmp_path / "state.json")
    mgr = LLMChainManager(raw_entries=[("gemini", "model-1"), ("groq", "model-2")], check_models_list=False)
    set_chain_manager(mgr)
    reset_run_metrics()

    call_counts = {"m1": 0, "m2": 0}

    async def mock_call_gemini(*args, **kwargs):
        call_counts["m1"] += 1
        raise RuntimeError("429 Rate limit. Please try again in 10s.")

    async def mock_call_groq(*args, **kwargs):
        call_counts["m2"] += 1
        return LLMResponse(thought="t", action="finish", args={"summary": "done"}, expected_outcome="o"), {"tokens": {}, "latency_ms": 10, "provider": "groq", "model": "model-2"}

    with patch("agent.llm.call_gemini", side_effect=mock_call_gemini), \
         patch("agent.llm.call_groq", side_effect=mock_call_groq), \
         patch("config.LLM_STATE_FILE", test_state), \
         patch("asyncio.sleep") as mock_sleep:
         
        action, usage = await generate_action("test")
        
    assert call_counts["m1"] == 1
    assert call_counts["m2"] == 1
    assert mgr.entries[0].is_exhausted
    assert mgr.entries[0].exhaustion_reason == "Gemini retryDelay 10.0s"
    assert usage["provider"] == "groq"
    mock_sleep.assert_not_called()

@pytest.mark.asyncio
async def test_shortest_wait_when_all_cooling_down(tmp_path):
    test_state = str(tmp_path / "state.json")
    mgr = LLMChainManager(raw_entries=[("gemini", "model-1"), ("groq", "model-2")], check_models_list=False)
    set_chain_manager(mgr)
    reset_run_metrics()

    # Pre-exhaust both entries with short per_minute
    now = time.time()
    mgr.entries[0].is_exhausted = True
    mgr.entries[0].exhaustion_expires_at = now + 15.0
    
    mgr.entries[1].is_exhausted = True
    mgr.entries[1].exhaustion_expires_at = now + 5.0

    call_counts = {"m2": 0}

    async def mock_call_groq(*args, **kwargs):
        call_counts["m2"] += 1
        return LLMResponse(thought="t", action="finish", args={"summary": "done"}, expected_outcome="o"), {"tokens": {}, "latency_ms": 10, "provider": "groq", "model": "model-2"}

    with patch("agent.llm.call_groq", side_effect=mock_call_groq), \
         patch("config.LLM_STATE_FILE", test_state), \
         patch("asyncio.sleep") as mock_sleep:
         
        _action, usage = await generate_action("test")
        
    assert call_counts["m2"] == 1
    mock_sleep.assert_called_once()
    args, _ = mock_sleep.call_args
    assert args[0] == pytest.approx(5.0, abs=0.1)
    assert usage["provider"] == "groq"

