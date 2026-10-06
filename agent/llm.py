import os
import time
import asyncio
from groq import AsyncGroq
from pydantic import BaseModel, ValidationError
import config

client = AsyncGroq(api_key=config.GROQ_API_KEY)

class LLMResponse(BaseModel):
    thought: str
    action: str
    args: dict
    expected_outcome: str

async def generate_action(system_prompt: str, history: list[dict], max_retries: int = 1) -> tuple[LLMResponse, dict]:
    messages = [{"role": "system", "content": system_prompt}] + history
    
    last_error = ""
    for attempt in range(max_retries + 1):
        if attempt > 0:
            messages.append({"role": "user", "content": f"Failed to parse JSON on previous attempt:\n{last_error}\nPlease fix and output valid JSON."})
        
        start_time = time.time()
        try:
            resp = await client.chat.completions.create(
                model=config.MODEL_NAME,
                messages=messages,
                response_format={"type": "json_object"}
            )
            latency = int((time.time() - start_time) * 1000)
            content = resp.choices[0].message.content
            usage = resp.usage
            tokens = {"prompt": usage.prompt_tokens, "completion": usage.completion_tokens}
            
            try:
                parsed = LLMResponse.model_validate_json(content)
                return parsed, {"tokens": tokens, "latency_ms": latency}
            except ValidationError as e:
                last_error = str(e)
                continue
                
        except Exception as e:
            if "429" in str(e):
                await asyncio.sleep(2 ** attempt)
                last_error = str(e)
                continue
            raise e
            
    raise ValueError(f"Failed to generate valid action after {max_retries} retries. Last error: {last_error}")
