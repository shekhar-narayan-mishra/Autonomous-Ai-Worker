import asyncio
import time
import pytest
from unittest.mock import patch, MagicMock

from agent.llm import (
    LLMChainManager, 
    ChainEntry, 
    generate_action, 
    LLMQuotaExhaustedError, 
    LLMResponse, 
    reset_run_metrics,
    set_chain_manager
)

class MockError(Exception):
    pass

@pytest.fixture
def mock_chain_manager():
    mgr = LLMChainManager(raw_entries=[
        ("gemini", "gemini-3.5-flash"),
        ("gemini", "gemini-3.5-pro"),
        ("groq", "llama-3-8b"),
    ], check_models_list=False)
    # Ensure they start clean
    for e in mgr.entries:
        e.is_exhausted = False
    
    set_chain_manager(mgr)
    return mgr

@pytest.fixture(autouse=True)
def reset_metrics():
    reset_run_metrics()
    
def test_fallback_selection(mock_chain_manager):
    # Test that get_active_entry iterates correctly
    entry = mock_chain_manager.get_active_entry()
    assert entry.provider == "gemini"
    assert entry.model == "gemini-3.5-flash"
    
    mock_chain_manager.mark_exhausted(entry, "daily", "test exhaustion")
    
    entry2 = mock_chain_manager.get_active_entry()
    assert entry2.provider == "gemini"
    assert entry2.model == "gemini-3.5-pro"

@pytest.mark.asyncio
async def test_authentication_failure_disables_provider(mock_chain_manager, monkeypatch):
    # If the first model gets a 401, the entire 'gemini' provider should be disabled, falling back to groq.
    async def mock_call_gemini(*args, **kwargs):
        raise MockError("HTTP 401 Unauthorized: Invalid API key")
    
    monkeypatch.setattr("agent.llm.call_gemini", mock_call_gemini)
    
    # We must also mock Groq so the loop doesn't fail on it
    async def mock_call_groq(*args, **kwargs):
        return LLMResponse(thought="t", action="a", args={}, expected_outcome="e"), {"tokens": {}, "latency_ms": 100, "provider": "groq", "model": "llama-3-8b"}
        
    monkeypatch.setattr("agent.llm.call_groq", mock_call_groq)
    
    action, usage = await generate_action("test prompt")
    
    # The first two entries were gemini, they should both be exhausted
    assert mock_chain_manager.entries[0].is_exhausted
    assert "Provider Auth" in mock_chain_manager.entries[0].exhaustion_reason
    
    assert mock_chain_manager.entries[1].is_exhausted
    assert "Provider Auth" in mock_chain_manager.entries[1].exhaustion_reason
    
    # It successfully fell back to groq
    assert usage["provider"] == "groq"
    
@pytest.mark.asyncio
async def test_invalid_model_disables_only_model(mock_chain_manager, monkeypatch):
    # 404 should only disable the specific model
    async def mock_call_gemini(model, *args, **kwargs):
        if model == "gemini-3.5-flash":
            raise MockError("HTTP 404 Model not found")
        return LLMResponse(thought="t", action="a", args={}, expected_outcome="e"), {"tokens": {}, "latency_ms": 100, "provider": "gemini", "model": "gemini-3.5-pro"}

    monkeypatch.setattr("agent.llm.call_gemini", mock_call_gemini)
    
    action, usage = await generate_action("test prompt")
    
    assert mock_chain_manager.entries[0].is_exhausted
    assert "Invalid Model" in mock_chain_manager.entries[0].exhaustion_reason
    
    assert not mock_chain_manager.entries[1].is_exhausted
    assert usage["provider"] == "gemini"
    assert usage["model"] == "gemini-3.5-pro"

@pytest.mark.asyncio
async def test_rate_limit_per_minute_cooldown(mock_chain_manager, monkeypatch):
    # Groq returns 429
    async def mock_call_gemini(*args, **kwargs):
        raise MockError("HTTP 429 Too Many Requests. Please try again in 5s.")
        
    monkeypatch.setattr("agent.llm.call_gemini", mock_call_gemini)
    
    # Let Groq succeed
    async def mock_call_groq(*args, **kwargs):
        return LLMResponse(thought="t", action="a", args={}, expected_outcome="e"), {"tokens": {}, "latency_ms": 100, "provider": "groq", "model": "llama-3-8b"}
    monkeypatch.setattr("agent.llm.call_groq", mock_call_groq)
    
    # Fast-forward sleep so we don't actually wait
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    
    action, usage = await generate_action("test prompt")
    
    # The first model retried and then was marked per_minute exhausted
    assert mock_chain_manager.entries[0].is_exhausted
    assert mock_chain_manager.entries[0].exhaustion_expires_at > time.time()
    
    # The second model also hit 429 (since mock applies to all gemini)
    assert mock_chain_manager.entries[1].is_exhausted
    
    # Fell back to groq
    assert usage["provider"] == "groq"

@pytest.mark.asyncio
async def test_all_configurations_exhausted(mock_chain_manager, monkeypatch):
    # Make all providers fail with 401
    async def mock_fail(*args, **kwargs):
        raise MockError("401 Unauthorized")
        
    monkeypatch.setattr("agent.llm.call_gemini", mock_fail)
    monkeypatch.setattr("agent.llm.call_groq", mock_fail)
    
    with pytest.raises(LLMQuotaExhaustedError) as exc:
        await generate_action("test prompt")
        
    msg = str(exc.value)
    assert "Provider Auth/Billing Error" in msg

class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super(AsyncMock, self).__call__(*args, **kwargs)

@pytest.mark.asyncio
async def test_cooldown_expiry(mock_chain_manager):
    # Manually exhaust the first entry but set its expiry to the past
    mock_chain_manager.entries[0].is_exhausted = True
    mock_chain_manager.entries[0].exhaustion_expires_at = time.time() - 10
    
    # It should become available again
    entry = mock_chain_manager.get_active_entry()
    assert entry == mock_chain_manager.entries[0]
    assert not entry.is_exhausted
