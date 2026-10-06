import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pydantic import BaseModel
from agent.llm import call_groq

class DummySchema(BaseModel):
    dummy: str

@pytest.mark.asyncio
async def test_groq_reasoning_kwargs():
    with patch("agent.llm.config.GROQ_API_KEY", "dummy_key"):
        with patch("groq.AsyncGroq") as MockGroq:
            mock_client_instance = MockGroq.return_value
            mock_create = AsyncMock()
            
            # Setup normal response
            mock_choice = MagicMock()
            mock_choice.message.content = '{"dummy": "test"}'
            mock_choice.finish_reason = "stop"
            mock_create.return_value.choices = [mock_choice]
            mock_create.return_value.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
            mock_client_instance.chat.completions.create = mock_create
            
            # BEFORE (non-gpt-oss model) -> should not have reasoning_effort
            await call_groq("llama-3.1-8b-instant", [{"role": "user", "content": "hello"}], DummySchema)
            kwargs_before = mock_create.call_args.kwargs
            assert "reasoning_effort" not in kwargs_before
            assert "max_completion_tokens" not in kwargs_before
            
            # qwen/deepseek model -> should have max_completion_tokens but NO reasoning_effort
            await call_groq("qwen/qwen3.8-27b", [{"role": "user", "content": "hello"}], DummySchema)
            kwargs_qwen = mock_create.call_args.kwargs
            assert "reasoning_effort" not in kwargs_qwen
            assert kwargs_qwen.get("max_completion_tokens") == 400
            
            # gpt-oss model -> should have reasoning_effort and max_completion_tokens
            await call_groq("openai/gpt-oss-120b", [{"role": "user", "content": "hello"}], DummySchema)
            kwargs_after = mock_create.call_args.kwargs
            assert kwargs_after.get("reasoning_effort") == "low"
            assert kwargs_after.get("max_completion_tokens") == 400

@pytest.mark.asyncio
async def test_groq_retry_on_length():
    with patch("agent.llm.config.GROQ_API_KEY", "dummy_key"):
        with patch("groq.AsyncGroq") as MockGroq:
            mock_client_instance = MockGroq.return_value
            mock_create = AsyncMock()
            
            # Setup first response: length limit hit, empty content
            mock_choice_1 = MagicMock()
            mock_choice_1.message.content = ""
            mock_choice_1.finish_reason = "length"
            
            # Setup second response: success
            mock_choice_2 = MagicMock()
            mock_choice_2.message.content = '{"dummy": "test"}'
            mock_choice_2.finish_reason = "stop"
            
            resp1 = MagicMock()
            resp1.choices = [mock_choice_1]
            
            resp2 = MagicMock()
            resp2.choices = [mock_choice_2]
            resp2.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
            
            mock_create.side_effect = [resp1, resp2]
            mock_client_instance.chat.completions.create = mock_create
            
            await call_groq("openai/gpt-oss-120b", [{"role": "user", "content": "hello"}], DummySchema)
            
            # Should have called create twice
            assert mock_create.call_count == 2
            
            # First call should have max_completion_tokens=400
            assert mock_create.call_args_list[0].kwargs.get("max_completion_tokens") == 400
            
            # Second call should have max_completion_tokens=800
            assert mock_create.call_args_list[1].kwargs.get("max_completion_tokens") == 800
