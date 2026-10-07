import time
from unittest.mock import patch

import pytest

from agent.llm import (
    _SUPPORTED_MODELS_CACHE,
    LLMChainManager,
    save_llm_state,
)


@pytest.mark.asyncio
async def test_first_step_no_models_list(monkeypatch):
    # clear cache
    _SUPPORTED_MODELS_CACHE.clear()
    
    # create chain manager with check_models_list=False (default behavior)
    mgr = LLMChainManager(check_models_list=False)
    
    # check that _SUPPORTED_MODELS_CACHE is still empty
    assert len(_SUPPORTED_MODELS_CACHE) == 0

def test_cache_hits_state(monkeypatch):
    # setup state
    _SUPPORTED_MODELS_CACHE.clear()
    save_llm_state({
        "models_cache_gemini": {
            "models": ["gemini-3.5-flash"],
            "expires_at": time.time() + 86400
        }
    })
    
    from agent.llm import get_supported_models
    with patch("agent.llm.logger.warning") as mock_warn:
        models = get_supported_models("gemini")
        assert "gemini-3.5-flash" in models
        mock_warn.assert_not_called()
