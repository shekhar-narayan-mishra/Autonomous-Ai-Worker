import asyncio
import time
import sys
from pydantic import BaseModel

import config
from agent.llm import (
    ChainEntry,
    get_supported_models,
    is_entry_exhausted,
    record_entry_exhaustion,
    classify_429,
    call_gemini,
    call_groq,
    call_openrouter,
    LLMResponse
)

class TinyResponse(BaseModel):
    status: str

async def check_entry(provider: str, model: str) -> dict:
    key = f"{provider}/{model}"
    
    # Check persisted exhaustion state
    is_ex, reason, rem = is_entry_exhausted(provider, model)
    if is_ex:
        rem_str = f"{int(rem // 3600)}h {int((rem % 3600) // 60)}m" if rem > 3600 else f"{int(rem)}s"
        print(f"[EXHAUSTED] {key} - Reason: {reason} (expires in {rem_str})")
        return {"key": key, "status": "EXHAUSTED", "reason": reason}

    messages = [{"role": "user", "content": 'Respond with valid JSON: {"thought": "health check", "action": "health_check", "args": {}, "expected_outcome": "ok"}'}]

    try:
        if provider == "gemini":
            res, usage = await call_gemini(model, messages, schema=LLMResponse)
        elif provider == "groq":
            res, usage = await call_groq(model, messages, schema=LLMResponse)
        elif provider == "openrouter":
            res, usage = await call_openrouter(model, messages, schema=LLMResponse)
        else:
            print(f"[ERROR] {key} - Unknown provider")
            return {"key": key, "status": "ERROR", "reason": "Unknown provider"}

        print(f"[OK] {key} - Latency: {usage['latency_ms']}ms | Tokens: {usage['tokens']}")
        return {"key": key, "status": "OK", "latency_ms": usage['latency_ms']}

    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "rate limit" in err_str.lower() or "resource_exhausted" in err_str.lower():
            ex_type, ra, reason = classify_429(provider, e)
            if ex_type == "daily":
                record_entry_exhaustion(provider, model, "daily", reason, retry_after=ra)
                print(f"[EXHAUSTED] {key} - Reason: {reason}")
                return {"key": key, "status": "EXHAUSTED", "reason": reason}
            else:
                record_entry_exhaustion(provider, model, "per_minute", reason, retry_after=ra)
                ra_str = f"{ra:.1f}s" if ra else "unknown"
                print(f"[RATE LIMITED (429)] {key} - Reason: {reason} (retry-after: {ra_str})")
                return {"key": key, "status": "429_PER_MINUTE", "reason": reason}
        else:
            print(f"[ERROR] {key} - {err_str}")
            return {"key": key, "status": "ERROR", "reason": err_str}

async def main():
    print("=" * 60)
    print("LLM CHAIN HEALTH CHECK (agent.llm_check)")
    print("=" * 60)

    # 1. Inspect supported models from providers
    print("\n--- Model Discovery (models.list) ---")
    active_entries: list[tuple[str, str]] = []
    for provider, model in config.CHAIN_ENTRIES:
        supported = get_supported_models(provider)
        if supported and model not in supported:
            print(f"[DROPPED] {provider}/{model} - Not returned in {provider}'s models.list()")
        else:
            active_entries.append((provider, model))

    print(f"\n--- Checking {len(active_entries)} Active Chain Entries ---")
    results = []
    for provider, model in active_entries:
        res = await check_entry(provider, model)
        results.append(res)
        await asyncio.sleep(1.0)  # gentle spacing between test calls

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    ok_count = sum(1 for r in results if r["status"] == "OK")
    ex_count = sum(1 for r in results if r["status"] == "EXHAUSTED")
    err_count = sum(1 for r in results if r["status"] in ("ERROR", "429_PER_MINUTE"))
    print(f"Total checked: {len(results)} | OK: {ok_count} | Exhausted: {ex_count} | Error/RateLimited: {err_count}")

if __name__ == "__main__":
    asyncio.run(main())
