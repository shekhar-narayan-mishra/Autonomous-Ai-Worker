import pytest
import asyncio
from unittest.mock import patch
from pydantic import BaseModel

from agent.llm import (
    LLMChainManager,
    ChainEntry,
    generate_action,
    set_chain_manager,
    reset_run_metrics,
    get_run_metrics,
    LLMQuotaExhaustedError,
    LLMResponse
)

@pytest.mark.asyncio
async def test_fallback_on_daily_quota(tmp_path):
    """Test that daily quota exhaustion marks entry exhausted and falls back to next chain entry."""
    test_state = str(tmp_path / "state.json")
    
    mgr = LLMChainManager(raw_entries=[("gemini", "model-1"), ("groq", "model-2")], check_models_list=False)
    set_chain_manager(mgr)
    reset_run_metrics()

    call_counts = {"m1": 0, "m2": 0}

    async def mock_call_gemini(model, messages, schema):
        call_counts["m1"] += 1
        raise RuntimeError("429 RESOURCE_EXHAUSTED. details: [{'violations': [{'quotaId': 'GenerateRequestsPerDay-FreeTier'}]}]")

    async def mock_call_groq(model, messages, schema):
        call_counts["m2"] += 1
        return LLMResponse(
            thought="success from groq",
            action="finish",
            args={},
            expected_outcome="done"
        ), {
            "tokens": {"prompt": 100, "completion": 20},
            "latency_ms": 15,
            "provider": "groq",
            "model": model
        }

    with patch("agent.llm.call_gemini", side_effect=mock_call_gemini), \
         patch("agent.llm.call_groq", side_effect=mock_call_groq), \
         patch("config.LLM_STATE_FILE", test_state):
        action, usage = await generate_action("System prompt", [{"role": "user", "content": "Task"}])

    assert call_counts["m1"] == 1
    assert call_counts["m2"] == 1
    assert action.action == "finish"
    assert usage["provider"] == "groq"

    metrics = get_run_metrics()
    assert metrics.model_switches >= 1
    assert "groq" in metrics.providers_used
    assert metrics.status == "ok"
    assert mgr.entries[0].is_exhausted is True
    assert mgr.entries[1].is_exhausted is False

@pytest.mark.asyncio
async def test_backoff_on_per_minute_429(tmp_path):
    """Test that per-minute 429 backs off and retries on the same entry without switching."""
    test_state = str(tmp_path / "state.json")
    
    mgr = LLMChainManager(raw_entries=[("groq", "model-1")], check_models_list=False)
    set_chain_manager(mgr)
    reset_run_metrics()

    call_count = 0

    async def mock_call_groq(model, messages, schema):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("429 Rate limit reached on requests per minute (RPM). Please try again in 0.05s.")
        return LLMResponse(
            thought="recovered after rpm",
            action="browser",
            args={"command": "click"},
            expected_outcome="clicked"
        ), {
            "tokens": {"prompt": 120, "completion": 25},
            "latency_ms": 20,
            "provider": "groq",
            "model": model
        }

    with patch("agent.llm.call_groq", side_effect=mock_call_groq), \
         patch("config.LLM_STATE_FILE", test_state):
        action, usage = await generate_action("System prompt", [{"role": "user", "content": "Task"}])

    assert call_count == 2
    assert action.action == "browser"
    assert usage["provider"] == "groq"

    metrics = get_run_metrics()
    assert metrics.rate_limit_429_count == 1
    assert metrics.model_switches == 0
    assert metrics.successful_calls == 1

@pytest.mark.asyncio
async def test_all_exhausted_abort(tmp_path):
    """Test that when all entries are exhausted, an infra error is raised with status llm_quota_exhausted."""
    test_state = str(tmp_path / "state.json")
    
    mgr = LLMChainManager(raw_entries=[("gemini", "model-1"), ("groq", "model-2")], check_models_list=False)
    set_chain_manager(mgr)
    reset_run_metrics()

    async def mock_call_gemini(model, messages, schema):
        raise RuntimeError("429 RESOURCE_EXHAUSTED. details: [{'violations': [{'quotaId': 'GenerateRequestsPerDay-FreeTier'}]}]")

    async def mock_call_groq(model, messages, schema):
        raise RuntimeError("429 Rate limit reached on tokens per day (TPD): Limit 200000.")

    with patch("agent.llm.call_gemini", side_effect=mock_call_gemini), \
         patch("agent.llm.call_groq", side_effect=mock_call_groq), \
         patch("config.LLM_STATE_FILE", test_state):
        with pytest.raises(LLMQuotaExhaustedError):
            await generate_action("System prompt", [{"role": "user", "content": "Task"}])

    metrics = get_run_metrics()
    assert metrics.status == "llm_quota_exhausted"
    assert metrics.successful_calls == 0
